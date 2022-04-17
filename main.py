import requests
import json
import logging

from web3 import Web3

from config import api_keys, addresses, WEB3_BY_NETWORK
from ContractStorage import ContractStorage

class Transaction:
    def __init__(self, tx_hash):
        self.from_covalent = False
        self.hash = tx_hash

    @staticmethod
    def parse_covalent_tx(tx_info):
        tx = Transaction(tx_info["tx_hash"])

        tx.successful = tx_info["successful"]

        tx.from_addr = tx_info["from_address"]
        tx.from_addr_label = tx_info["from_address_label"]
        tx.to_addr = tx_info["to_address"]
        tx.to_addr_label = tx_info["to_address_label"]

        tx.fees_paid = tx_info["fees_paid"]
        tx.gas_spent = tx_info["gas_spent"]

        tx.log_events = tx_info["log_events"]
        tx.from_covalent = True
        return tx

    def decipher(self, our_address):
        """
        custom interpretation of events in a transaction
            for each type of event we see, add interpretation logic here

        ignore any events unrelated to our address

        for example, an airdrop can initiate transfers to many addresses in one
            transaction, and our address would only be one of the events in the transaction

        another example, intermediate swaps in a swap transaction, we only care about what
            leaves our wallet, and what enters our wallet
        """
        if not self.from_covalent:
            return

        # addresses from covalent are lowercase form, so for comparison purposes, lowercase our address
        our_address = our_address.lower()

        for event in self.log_events:
            # custom handling of events in a transaction
            if not event["decoded"]:
                logging.info("unknown event")
                continue

            event_name = event["decoded"]["name"]
            if event_name == "Transfer":
                from_address, to_address, value = [p["value"] for p in event["decoded"]["params"]]
                value = float(value) / 10**int(event['sender_contract_decimals'])
                if from_address == our_address:
                    logging.info(f"OUT {value} {event['sender_address_label']} to {to_address}")
                elif to_address == our_address:
                    logging.info(f"IN {value} {event['sender_address_label']} from {self.from_addr_label}")
                else: # not related to our address
                    continue
            elif event_name == "Approval":
                owner, spender, value = [p["value"] for p in event["decoded"]["params"]]

                if owner != our_address:
                    # don't have to worry about approvals not related to our address
                    continue

                # spender is not important
                # TODO : only relevance to us is the gas cost
                logging.info(f"approve token: {event['sender_address_label']}")
            else:
                logging.info(f"*** {event['decoded']['name']}")

class TransactionOrganizer:
    def __init__(self, address):
        """
        address (str) : get transactions for this address
        """
        self.address = address

    def _switch_network(self, network):
        self.web3 = WEB3_BY_NETWORK.get(network)

        if not self.web3:
            assert False, f"unsupported network: {network}"

        self.network = network

    def get_transactions_old(self):
        """
        returns list of transaction hashes, pulling from snowtrace / etherscan

        ** UNUSED **
        """
        if self.network == "AVAX":
            get_txns_url = f"https://api.snowtrace.io/api?module=account&action=txlist&address={self.address}&sort=asc&apikey={api_keys['SNOWTRACE']}"
        else:
            get_txns_url = f"https://api.etherscan.io/api?module=account&action=txlist&address={self.address}&sort=asc&apikey={api_keys['ETHERSCAN']}"
        res = requests.get(get_txns_url).json()["result"]
        txns = [Transaction(t["hash"]) for t in txns]

        # TODO : not handling incoming ERC20 token transfers
        return txns

    def get_transactions(self):
        """
        returns list of transaction hashes, pulling from covalent
        """
        chain_id = 43114 if self.network == "AVAX" else 1

        get_txns_url = f"https://api.covalenthq.com/v1/{chain_id}/address/{self.address}/transactions_v2/?&key={api_keys['COVALENT']}"
        res = requests.get(get_txns_url).json()["data"]

        # TODO : currently not handling large number of transactions
        assert not res["pagination"]["has_more"]

        txns = [Transaction.parse_covalent_tx(t) for t in res["items"]]
        return txns

    def extract(self, network):
        self._switch_network(network)
        txns = self.get_transactions()

        logging.info(f"FOUND {len(txns)} {self.network} TRANSACTIONS FOR ADDRESS {self.address}")
        logging.info(f"DISPLAYING FROM EARLIEST TO LATEST\n")

        for txn in txns:
            txn_details = self.web3.eth.get_transaction(txn.hash)
            from_address = txn_details["from"]
            to_address = txn_details["to"]
            fn_selector = txn_details["input"][:10]

            if from_address == self.address == to_address:
                logging.info("[SELF]")

            elif from_address == self.address:
                contract = ContractStorage.get_contract(to_address, self.network)
                fn = ContractStorage.get_fn_name(contract, fn_selector)
                if fn == "transfer":
                    token_symbol = contract.functions.symbol().call()
                    logging.info(f"[ OUT] {to_address} {fn} {token_symbol}")
                else:
                    logging.info(f"[ OUT] {to_address} {fn}")

            elif to_address == self.address:
                # transfer from a non-contract address
                logging.info(f"[  IN] {from_address} (transferred from)")

            else:
                # contract address cannot be in [from] address
                # contract cannot initiate a send, requires interaction from a user
                #   a person -> interacts with -> a contract
                #     -> during contract execution, tokens are transferred to me
                #
                # for example,
                #   > Coinbase calls Transfer on an ERC20 token contract
                #   > the ERC20 token(s) appear at my address

                # etherscan / snowtrace doesn't show these interactions when listing transactions,
                #   but they can be obtained by querying ERC20 token transfers

                contract = ContractStorage.get_contract(to_address, self.network)
                fn = ContractStorage.get_fn_name(contract, fn_selector)

                if fn == "transfer":
                    # likely an ERC20 token contract interaction
                    token_symbol = contract.functions.symbol().call()
                    logging.info(f"[ *IN] {from_address} {fn} {token_symbol}")
                else:
                    logging.info(f"[ *IN] {from_address} {fn}")

            # log some more details about the transaction
            # currently does nothing if txn data was not obtained from covalent
            txn.decipher(self.address)

        print()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s :: %(levelname)s :: %(message)s')
    if not addresses:
        print("no addresses provided in config")
    for addr in addresses:
        t = TransactionOrganizer(addr)
        # t.extract("AVAX")
        t.extract("ETHE")
