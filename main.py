import requests
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from collections import defaultdict

from web3 import Web3

from config import api_keys, addresses, WEB3_BY_NETWORK
from ContractStorage import ContractStorage

class Wallet:
    def __init__(self, address, network):
        self.address = address
        self.network = network
        self.transfers = [] # list of TokenTransfer
        self.balances = defaultdict(int) # token symbol -> balance

    def add_transfers(self, transfers):
        self.transfers.extend(transfers)

    def add_transfer(self, transfer):
        self.transfers.append(transfer)

    def replay_transfers(self):
        """
        - updates self.balances to the ending balances of all tokens in the
          wallet after all transfers are executed.
        - replay the list of associated transfers onto the wallet, with
          all token balances starting at 0
        - assert that the token balances are always non-negative after each tx

        - logging
            - for each transaction, log the incoming and outgoing transfers,
              as well as fees paid
            - log the ending balances of the tokens in the wallet

        - note: starts with empty self.balances, and assumes self.transfers is
                the full list of incoming and outgoing transfers of the wallet.
                does not need to be sorted.

        """
        self.balances = defaultdict(int)

        # within each transaction, list internal transactions last (internal tx fees == 0)
        sequence = sorted(self.transfers, key=lambda t : (t.timestamp, -t.fees))

        seen_tx_hashes = set()

        for tx in sequence:
            if tx.tx_hash not in seen_tx_hashes:
                # multiple transfers may be in a transaction,
                # deduct gas only once for the transaction
                logging.info("~" * 80)
                logging.info(f"tx hash: {tx.tx_hash}") # start of a new group of transfers
                logging.info(f"tx time: {tx.timestamp}")
                if tx.from_addr.lower() == self.address.lower():
                    self.balances[tx.network] -= tx.fees
                    logging.info(f"fees paid: {tx.fees} {network}")
                seen_tx_hashes.add(tx.tx_hash)

                # we are at a new tx, so validate ending balances
                for token, bal in self.balances.items():
                    assert bal >= 0, f"negative balance found for {token} token: {bal}..."

            if not tx.value:
                continue

            if tx.from_addr.lower() == self.address.lower():
                self.balances[tx.token_symbol] -= tx.value
                logging.info(f"- {tx.value} {tx.token_symbol}")
            elif tx.to_addr.lower() == self.address.lower():
                self.balances[tx.token_symbol] += tx.value
                logging.info(f"+ {tx.value} {tx.token_symbol}")

            logging.debug(f"cumulative balance ({tx.token_symbol}): {self.balances[tx.token_symbol]}")

        logging.info("=== token balances ===")
        for token, bal in self.balances.items():
            logging.info(f"{token}: {self.balances[token]}")
            assert bal >= 0, f"negative balance found for {token} token: {bal}..."
        logging.info("======================")

class TokenTransfer:
    def __init__(self, from_addr, to_addr, value,
                 gas_used, gas_price, network,
                 token_symbol, token_decimals, token_type,
                 timestamp, tx_hash):
        self.from_addr = from_addr
        self.to_addr = to_addr
        if token_decimals:
            self.value = Decimal(value) / 10**int(token_decimals)
        else:
            self.value = Decimal(value)
        self.fees = Web3.fromWei(int(gas_used) * int(gas_price), "ether")
        self.network = network
        self.token_symbol = token_symbol
        self.token_type = token_type
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
        tx.timestamp = datetime.strptime(tx_info["block_signed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

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
        returns list of Transaction, pulling from snowtrace / etherscan

            sort order is earliest to latest transaction

        only includes smart contract interactions initiated by our address,
          does not include token transfers that happened during smart contract interactions
          initiated by others. token transfers are also not directly returned, they need to
          be extracted from the log events.

        ** UNUSED **
        """
        if self.network == "AVAX":
            get_txns_url = f"https://api.snowtrace.io/api?module=account&action=txlist&address={self.address}&sort=asc&apikey={api_keys['SNOWTRACE']}"
        else:
            get_txns_url = f"https://api.etherscan.io/api?module=account&action=txlist&address={self.address}&sort=asc&apikey={api_keys['ETHERSCAN']}"
        res = requests.get(get_txns_url).json()["result"]

        txns = []
        for t in res:
            txn = Transaction(t["hash"])
            txn.gas_spent = t["gasUsed"]
            txn.timestamp = datetime.fromtimestamp(int(t["timeStamp"]), tz=timezone.utc)
            txns.append(txn)

        return txns

    def get_transactions(self):
        """
        returns list of Transaction, pulling from covalent

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
                gas_price=t["gasPrice"],
                network=self.network,
                token_symbol=t["tokenSymbol"],
                token_decimals=t["tokenDecimal"],
                token_type="ERC20",
                timestamp=datetime.fromtimestamp(int(t["timeStamp"]), tz=timezone.utc),
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
                gas_price=t["gasPrice"],
                network=self.network,
                token_symbol=t["tokenSymbol"],
                token_decimals=t["tokenDecimal"],
                token_type="ERC721",
                timestamp=datetime.fromtimestamp(int(t["timeStamp"]), tz=timezone.utc),
                tx_hash=t["hash"]
            ) for t in res
        ]

    def get_internal_transactions(self):
        """
        returns list of TokenTransfer, pulling from snowtrace / etherscan

            sort order is earliest to latest transaction
        """
        if self.network == "AVAX":
            get_internal_txns_url = f"https://api.snowtrace.io/api?module=account&action=txlistinternal&address={self.address}&sort=asc&apikey={api_keys['SNOWTRACE']}"
        else:
            get_internal_txns_url = f"https://api.etherscan.io/api?module=account&action=txlistinternal&address={self.address}&sort=asc&apikey={api_keys['ETHERSCAN']}"
        res = requests.get(get_internal_txns_url).json()["result"]

        return [
            TokenTransfer(
                from_addr=t["from"],
                to_addr=t["to"],
                value=Web3.fromWei(int(t["value"]), 'ether'),
                gas_used=0, # we pay no gas for the internal transactions
                gas_price=0,
                network=self.network,
                token_symbol=self.network,
                token_decimals=None,
                token_type="native",
                timestamp=datetime.fromtimestamp(int(t["timeStamp"]), tz=timezone.utc),
                tx_hash=t["hash"] # internal txns have no tx hash, only a parent tx hash
            ) for t in res
        ]

    def extract(self, network):
        logging.info(f"EXTRACTING TRANSACTIONS ({network}) AT {self.address}")
        self._switch_network(network)

        wallet = Wallet(self.address, self.network)

        done_hashes = set()

        # [1] fetch the simple ERC-20 token transfers
        erc20_transfers = self.get_erc20_transfers()
        for t in erc20_transfers:
            if self.address.lower() == t.to_addr.lower():
                logging.debug(f"[  IN] {t.value} {t.token_symbol}")
            elif self.address.lower() == t.from_addr.lower():
                logging.debug(f"[ OUT] {t.value} {t.token_symbol}")
            done_hashes.add(t.tx_hash)
        wallet.add_transfers(erc20_transfers)

        # [2] fetch the ERC-721 token transfers
        erc721_transfers = self.get_erc721_transfers()
        for t in self.get_erc721_transfers():
            if self.address.lower() == t.to_addr.lower():
                logging.debug(f"[  IN] NFT - {t.value} {t.token_symbol}")
            elif self.address.lower() == t.from_addr.lower():
                logging.debug(f"[ OUT] NFT - {t.value} {t.token_symbol}")
            done_hashes.add(t.tx_hash)
        wallet.add_transfers(erc721_transfers)

        # [3-A] handle network native token transfers (in internal transactions)
        internal_transactions = self.get_internal_transactions()
        for t in internal_transactions:
            if self.address.lower() == t.to_addr.lower():
                logging.debug(f"[i IN] {t.value} {self.network} from {t.from_addr}")
            else:
                assert self.address.lower() == t.from_addr.lower()
                logging.debug(f"[iOUT] {t.value} {self.network} to {t.to_addr}")
        wallet.add_transfers(internal_transactions)

        txns = self.get_transactions()
        for txn in txns:
            txn_details = self.web3.eth.get_transaction(txn.hash)
            from_address = txn_details["from"]
            to_address = txn_details["to"]
            fn_selector = txn_details["input"][:10]

            # [3-B] handle network native token transfers
            native_token_transferred = Web3.fromWei(txn_details['value'], 'ether')
            if native_token_transferred:
                val = native_token_transferred

                if self.address == to_address:
                    logging.debug(f"[  IN] {val} {self.network} from {from_address}")
                else:
                    assert self.address == from_address
                    logging.debug(f"[ OUT] {val} {self.network} to {to_address}")

                wallet.add_transfer(TokenTransfer(
                    from_addr=from_address,
                    to_addr=to_address,
                    value=val,
                    gas_used=txn.gas_spent,
                    gas_price=txn_details["gasPrice"],
                    network=self.network,
                    token_symbol=self.network,
                    token_decimals=None,
                    token_type="native",
                    timestamp=txn.timestamp,
                    tx_hash=txn.hash
                ))

                continue

            # already handled IN and OUT relevant to our address for these transactions
            if txn.hash in done_hashes:
                continue

            # remaining transactions are only needed to account for gas usage
            wallet.add_transfer(TokenTransfer(
                from_addr=from_address,
                to_addr=to_address,
                value=0,
                gas_used=txn.gas_spent,
                gas_price=txn_details["gasPrice"],
                network=self.network,
                token_symbol=self.network,
                token_decimals=None,
                token_type=None,
                timestamp=txn.timestamp,
                tx_hash=txn.hash
            ))

            # [4] handle SELF messages
            if from_address == self.address == to_address:
                logging.debug("[SELF]")

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

        return wallet

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s :: %(levelname)s :: %(message)s')
    if not addresses:
        print("no addresses provided in config")
    for addr in addresses:
        t = TransactionOrganizer(addr)

        for network in ["AVAX", "ETHE"]:
            wallet = t.extract(network)
            wallet.replay_transfers()

