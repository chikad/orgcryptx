import requests
import json
import logging

from web3 import Web3

from config import api_keys, addresses, WEB3_BY_NETWORK
from ContractStorage import ContractStorage

class TokenTransfer:
    def __init__(self, from_addr, to_addr, value, gas_used,
                 token_symbol, token_decimals,
                 timestamp, tx_hash):
        self.from_addr = from_addr
        self.to_addr = to_addr
        self.value = float(value) / 10**int(token_decimals)
        self.gas_used = gas_used
        self.token_symbol = token_symbol
        self.timestamp = timestamp
        self.tx_hash = tx_hash

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

            sort order is earliest to latest transaction

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

            sort order is earliest to latest transaction
        """
        chain_id = 43114 if self.network == "AVAX" else 1

        get_txns_url = f"https://api.covalenthq.com/v1/{chain_id}/address/{self.address}/transactions_v2/?&key={api_keys['COVALENT']}"
        res = requests.get(get_txns_url).json()["data"]

        # TODO : currently not handling large number of transactions
        assert not res["pagination"]["has_more"]

        txns = [Transaction.parse_covalent_tx(t) for t in res["items"]][::-1]
        return txns

    def get_erc20_transfers(self):
        """
        get all ERC20 Token Transfer Events
        """
        if self.network == "AVAX":
            get_transfers_url = f"https://api.snowtrace.io/api?module=account&action=tokentx&address={self.address}&sort=asc&apikey={api_keys['SNOWTRACE']}"
        else:
            get_transfers_url = f"https://api.etherscan.io/api?module=account&action=tokentx&address={self.address}&sort=asc&apikey={api_keys['ETHERSCAN']}"
        res = requests.get(get_transfers_url).json()["result"]

        return [
            TokenTransfer(
                from_addr=t["from"],
                to_addr=t["to"],
                value=t["value"],
                gas_used=t["gasUsed"],
                token_symbol=t["tokenSymbol"],
                token_decimals=t["tokenDecimal"],
                timestamp=t["timeStamp"],
                tx_hash=t["hash"]
            ) for t in res
        ]

    def get_erc721_transfers(self):
        """
        get all ERC721 Token Transfer Events
        """
        if self.network == "AVAX":
            get_transfers_url = f"https://api.snowtrace.io/api?module=account&action=tokennfttx&address={self.address}&sort=asc&apikey={api_keys['SNOWTRACE']}"
        else:
            get_transfers_url = f"https://api.etherscan.io/api?module=account&action=tokennfttx&address={self.address}&sort=asc&apikey={api_keys['ETHERSCAN']}"
        res = requests.get(get_transfers_url).json()["result"]

        return [
            TokenTransfer(
                from_addr=t["from"],
                to_addr=t["to"],
                value=1,
                gas_used=t["gasUsed"],
                token_symbol=t["tokenSymbol"],
                token_decimals=t["tokenDecimal"],
                timestamp=t["timeStamp"],
                tx_hash=t["hash"]
            ) for t in res
        ]

    def extract(self, network):
        logging.info(f"EXTRACTING TRANSACTIONS AT {self.address}")
        self._switch_network(network)

        done_hashes = set()

        # [1] fetch the simple ERC-20 token transfers
        for t in self.get_erc20_transfers():
            if self.address.lower() == t.to_addr.lower():
                logging.debug(f"[  IN] {t.value} {t.token_symbol}")
            elif self.address.lower() == t.from_addr.lower():
                logging.debug(f"[ OUT] {t.value} {t.token_symbol}")
            done_hashes.add(t.tx_hash)

        # [2] fetch the ERC-721 token transfers
        for t in self.get_erc721_transfers():
            if self.address.lower() == t.to_addr.lower():
                logging.debug(f"[  IN] NFT - {t.value} {t.token_symbol}")
            elif self.address.lower() == t.from_addr.lower():
                logging.debug(f"[ OUT] NFT - {t.value} {t.token_symbol}")
            done_hashes.add(t.tx_hash)

        txns = self.get_transactions()

        for txn in txns:
            # already handled IN and OUT relevant to our address for these transactions
            if txn.hash in done_hashes:
                continue

            txn_details = self.web3.eth.get_transaction(txn.hash)
            from_address = txn_details["from"]
            to_address = txn_details["to"]
            fn_selector = txn_details["input"][:10]

            # [3] handle network native token transfers
            native_token_transferred = Web3.fromWei(txn_details['value'], 'ether')
            if native_token_transferred:
                val = native_token_transferred

                if self.address == to_address:
                    logging.debug(f"[  IN] {val} {self.network} from {from_address}")

                else:
                    assert self.address == from_address
                    logging.debug(f"[ OUT] {val} {self.network} to {to_address}")

                continue

            # [4] handle SELF messages
            if from_address == self.address == to_address:
                logging.debug("[SELF]")
                # TODO

            # [5] handle contract interactions
            # the remaining must be contract interactions since this is not a native token Transfer
            elif from_address == self.address:
                contract = ContractStorage.get_contract(to_address, self.network)
                fn = ContractStorage.get_fn_name(contract, fn_selector)
                if fn == "approve":
                    token_symbol = contract.functions.symbol().call()
                    logging.debug(f"[APPR] {fn} {token_symbol}")
                else:
                    # contract interaction with no associated IN or OUT transfers
                    #   for example, calling a function that allows (or stops) your loaned
                    #   tokens to be used as collateral that you to borrow against
                    # we are the ones interacting with the contract
                    logging.debug(f"[*</>] {to_address} {fn}")

            elif to_address == self.address:
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
                assert False, f"unexpected FROM address, tx_hash: {txn.hash}"

            else:
                # someone else interacted with a contract, we receive an incoming transfer
                logging.debug(f"ERC20, ERC721, and native token incoming transfers should already have been handled, tx_hash: {txn.hash}")

                # contract interaction with no associated IN or OUT transfers
                # someone else interacted with the contract, and we were "affected"
                # not sure what "affected" is yet...
                contract = ContractStorage.get_contract(to_address, self.network)
                fn = ContractStorage.get_fn_name(contract, fn_selector)
                logging.debug(f"[?</>]{from_address} {fn}")

        print("=" * 80)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s :: %(levelname)s :: %(message)s')
    if not addresses:
        print("no addresses provided in config")
    for addr in addresses:
        t = TransactionOrganizer(addr)
        t.extract("AVAX")
        t.extract("ETHE")
