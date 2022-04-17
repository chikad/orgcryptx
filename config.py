from web3 import Web3

api_keys = {
    "SNOWTRACE" : "",
    "ETHERSCAN" : "",
    "ALCHEMY"   : "",
    "COVALENT"  : "",
}

addresses = [
]

ABI_CACHE_PATH = "./abi_cache"

AVAX_RPC = "https://api.avax.network/ext/bc/C/rpc"
ETHE_RPC = f"https://eth-mainnet.alchemyapi.io/v2/{api_keys['ALCHEMY']}"

WEB3_BY_NETWORK = {
    "AVAX" : Web3(Web3.HTTPProvider(AVAX_RPC)),
    "ETHE" : Web3(Web3.HTTPProvider(ETHE_RPC)),
}
