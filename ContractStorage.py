import requests
import json
import os

from web3 import Web3
from web3.exceptions import ContractLogicError, BadFunctionCallOutput

from config import api_keys, WEB3_BY_NETWORK, ABI_CACHE_PATH

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

        with open("./ERC20-ABI.json") as f:
            cls.ERC20_ABI = f.read()

        with open("./ERC721-ABI.json") as f:
            cls.ERC721_ABI = f.read()

        cls.initialized = True

    @classmethod
    def _switch_network(cls, network):
        cls.web3 = WEB3_BY_NETWORK.get(network)

        if not cls.web3:
            assert False, f"unsupported network: {network}"

        cls.network = network

    @classmethod
    def guess_abi(cls, contract_address):
        """
        attempt to guess ABI as ERC20 or ERC721

        if neither seem to be correct, return contract with no ABI
        """
        contract = cls.web3.eth.contract(contract_address, abi=cls.ERC721_ABI)

        try:
            # test if the contract actually is ERC721 compatible
            # ownerOf only in ERC721
            contract.functions.ownerOf(0).call()
            return contract
        except BadFunctionCallOutput:
            pass
        except ContractLogicError:
            # probably not authorized to call, but this means we were able to call it
            return contract

        contract = cls.web3.eth.contract(contract_address, abi=cls.ERC20_ABI)

        try:
            # test if the contract actually is ERC20 compatible
            contract.functions.totalSupply().call()
            return contract
        except BadFunctionCallOutput:
            # doesn't seem to be ERC20 compatible, don't use ABI
            return cls.web3.eth.contract(contract.address)

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
            contract = cls.guess_abi(contract_address)
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
            try:
                return contract.get_function_by_selector(fn_selector).fn_name
            except:
                return fn_selector
        else:
            return fn_selector
