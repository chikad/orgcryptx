import requests
import json
import os

from web3 import Web3
from web3.exceptions import ContractLogicError

from config import api_keys, addresses

ABI_CACHE_PATH = "./abi_cache"

AVAX_RPC = "https://api.avax.network/ext/bc/C/rpc"
ETHE_RPC = f"https://eth-mainnet.alchemyapi.io/v2/{api_keys['ALCHEMY']}"

AVAX_WEB3 = Web3(Web3.HTTPProvider(AVAX_RPC))
ETHE_WEB3 = Web3(Web3.HTTPProvider(ETHE_RPC))

class Transaction:
    def __init__(self):
        pass

class ContractStorage:
    initialized = False

    @classmethod
    def init_cache(cls):
        if cls.initialized:
            return

        cls.cache = {}

        if not os.path.exists(ABI_CACHE_PATH):
            os.mkdir(ABI_CACHE_PATH)

        for abi_file in os.listdir(ABI_CACHE_PATH):
            with open(f"{ABI_CACHE_PATH}/{abi_file}") as f:
                abi = f.read()

            contract_address, network, _ = abi_file.split("_")
            cls._switch_network(network)

            if abi:
                cls.cache[contract_address] = cls.web3.eth.contract(contract_address, abi=abi)
            else:
                cls.cache[contract_address] = cls.web3.eth.contract(contract_address)

        cls.initialized = True

    @classmethod
    def _switch_network(cls, network):
        if network == "AVAX":
            cls.web3 = AVAX_WEB3
        elif network == "ETHE":
            cls.web3 = ETHE_WEB3
        else:
            assert False, f"unsupported network: {network}"

        cls.network = network

    @classmethod
    def _get_contract(cls, contract_address):
        if cls.network == "AVAX":
            get_contract_abi_url = f"https://api.snowtrace.io/api?module=contract&action=getabi&address={contract_address}&apikey={api_keys['SNOWTRACE']}"
        else:
            get_contract_abi_url = f"https://api.etherscan.io/api?module=contract&action=getabi&address={contract_address}&apikey={api_keys['ETHERSCAN']}"

        abi = requests.get(get_contract_abi_url).json()["result"]

        if abi != "Contract source code not verified":
            contract = cls.web3.eth.contract(contract_address, abi=abi)
        else:
            contract = cls.web3.eth.contract(contract_address)

        return contract

    @classmethod
    def _get_impl_contract_for_proxy(cls, contract):
        """
        attempt to get the underlying implementation contract for a proxy contract

        implementation address can often be found at standardized location, but
        this is not guaranteed

        since it can differ contract by contract, add previously seen implementation slots here

        if no implementation address can be found, just return the contract at the given address
        """
        # try reading the implementation variable (if it is public)
        try:
            impl_addr = contract.functions.implementation().call()
            return cls._get_contract(impl_addr)
        except ContractLogicError:
            # implementation function can only be called by admin,
            # so will try to get implementation from contract storage
            pass

        # https://ethereum.stackexchange.com/questions/103143/how-do-i-get-the-implementation-contract-address-from-the-proxy-contract-address
        potential_impl_slots = [
            # https://eips.ethereum.org/EIPS/eip-1967#logic-contract-address
            # standard _IMPLEMENTATION_SLOT as specified in ERC-1967
            Web3.toInt(Web3.keccak(text="eip1967.proxy.implementation")) - 1,

            # https://docs.zeppelinos.org/docs/2.1.0/pattern
            Web3.keccak(text="org.zeppelinos.proxy.implementation"),
        ]

        for impl_slot in potential_impl_slots:
            impl_addr_bytes = cls.web3.eth.get_storage_at(contract.address, impl_slot)
            if Web3.toInt(impl_addr_bytes) != 0:
                break
        else:
            # could not find implementation address
            return contract

        impl_addr = Web3.toChecksumAddress(Web3.toInt(impl_addr_bytes))

        return cls._get_contract(impl_addr)

    @classmethod
    def get_contract(cls, contract_address, network):
        if not cls.initialized:
            cls.init_cache()

        cls._switch_network(network)

        # checks cache first, before making api call
        contract = cls.cache.get(contract_address)
        if contract:
            return contract

        # not in cache, make api call and load
        contract = cls._get_contract(contract_address)

        if contract.abi and contract.find_functions_by_name("implementation"):
            # potential proxy contract, fetch the implementation contract
            contract = cls._get_impl_contract_for_proxy(contract)

        with open(f"{ABI_CACHE_PATH}/{contract_address}_{cls.network}_abi.json", "wt") as f:
            if contract.abi:
                json.dump(contract.abi, f, indent=4)

        cls.cache[contract_address] = contract
        return contract

    @classmethod
    def get_fn_name(cls, contract, fn_selector):
        """
        returns: the function name if verified abi exists,
                 else, just returns the fn_selector
        """
        if contract.abi is not None:
            return contract.get_function_by_selector(fn_selector).fn_name
        else:
            return fn_selector

class TransactionOrganizer:
    def __init__(self, address):
        """
        address (str) : get transactions for this address
        """
        self.address = address

    def _switch_network(self, network):
        if network == "AVAX":
            self.web3 = AVAX_WEB3
        elif network == "ETHE":
            self.web3 = ETHE_WEB3
        else:
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
    for addr in addresses:
        t = TransactionOrganizer(addr)
        t.extract("AVAX")
        t.extract("ETHE")
