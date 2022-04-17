import requests
import json

from web3 import Web3

from config import api_keys, addresses, WEB3_BY_NETWORK
from ContractStorage import ContractStorage

class Transaction:
    def __init__(self):
        pass

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
        txns = [t["hash"] for t in txns]

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

        txns = [t["tx_hash"] for t in res["items"]]
        return txns

    def extract(self, network):
        self._switch_network(network)
        txns = self.get_transactions()

        print(f"FOUND {len(txns)} {self.network} TRANSACTIONS FOR ADDRESS {self.address}")
        print(f"DISPLAYING FROM EARLIEST TO LATEST\n")

        for txn_hash in txns:
            txn_details = self.web3.eth.get_transaction(txn_hash)
            from_address = txn_details["from"]
            to_address = txn_details["to"]
            fn_selector = txn_details["input"][:10]

            if from_address == self.address == to_address:
                print("[SELF]")

            elif from_address == self.address:
                contract = ContractStorage.get_contract(to_address, self.network)
                fn = ContractStorage.get_fn_name(contract, fn_selector)
                if fn == "transfer":
                    token_symbol = contract.functions.symbol().call()
                    print("[ OUT]", to_address, fn, token_symbol)
                else:
                    print("[ OUT]", to_address, fn)

            elif to_address == self.address:
                # transfer from a non-contract address
                print(f"[  IN] {from_address} (transferred from)")

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
                    print(f"[ *IN]", from_address, fn, token_symbol)
                else:
                    print(f"[ *IN]", from_address, fn)

        print()

if __name__ == "__main__":
    if not addresses:
        print("no addresses provided in config")
    for addr in addresses:
        t = TransactionOrganizer(addr)
        t.extract("AVAX")
        t.extract("ETHE")
