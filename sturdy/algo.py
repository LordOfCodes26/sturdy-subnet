import math
from typing import cast

import bittensor as bt
from web3.constants import ADDRESS_ZERO

from sturdy.base.miner import BaseMinerNeuron
from sturdy.pools import (
    POOL_TYPES,
    PoolFactory,
    get_minimum_allocation,
)
from sturdy.protocol import AllocateAssets

THRESHOLD = 0.99  # used to avoid over-allocations


# NOTE: THIS IS JUST AN EXAMPLE - THIS IS NOT VERY OPTIMIZED
def naive_algorithm(self: BaseMinerNeuron, synapse: AllocateAssets) -> dict:
    bt.logging.debug(f"received request type: {synapse.request_type}")
    pools = cast(dict, synapse.assets_and_pools["pools"])
    for uid, pool in pools.items():
        pools[uid] = PoolFactory.create_pool(
            pool_type=pool.pool_type,
            web3_provider=self.w3,  # type: ignore[]
            user_address=(
                pool.user_address if pool.user_address != ADDRESS_ZERO else synapse.user_address
            ),  # TODO: is there a cleaner way to do this?
            contract_address=pool.contract_address,
        )
    total_assets_available = int(THRESHOLD * synapse.assets_and_pools["total_assets"])
    pools = cast(dict, synapse.assets_and_pools["pools"])
    supply_rate_sum = 0
    supply_rates = {}

    # sync pool parameters by calling smart contracts on chain
    for pool in pools.values():
        pool.sync(self.w3)

    # check the amounts that have been borrowed from the pools - and account for them
    minimums = {}
    for pool_uid, pool in pools.items():
        minimums[pool_uid] = get_minimum_allocation(pool)
    total_assets_available -= sum(minimums.values())
    balance = int(total_assets_available)  # obtain supply rates of pools - aave pool and sturdy silo
    # rates are determined by making on chain calls to smart contracts
    for pool in pools.values():
        bt.logging.debug(f"pool_contract_address: {pool.contract_address}")
        match pool.pool_type:
            case POOL_TYPES.DAI_SAVINGS:
                apy = pool.supply_rate()
                supply_rates[pool.contract_address] = apy
                bt.logging.debug(f"pool_apy: {apy}") 
                supply_rate_sum += apy
            case _:
                apy = pool.supply_rate(balance // len(pools))
                supply_rates[pool.contract_address] = apy
                bt.logging.debug(f"pool_apy: {apy}") 
                supply_rate_sum += apy
    supply_rate_dict = {
        pool_uid: calculate_apy(pools[pool_uid], balance, pools) for pool_uid in pools
    }
    bt.logging.debug(f"supply_rate_dict: {supply_rate_dict}")
    return {
        pool_uid: minimums[pool_uid] + math.floor((supply_rates[pool_uid] / supply_rate_sum) * balance) for pool_uid in pools
    }

def calculate_apy(pool, balance: int, pools: dict) -> int:
    """
    Calculate the Annual Percentage Yield (APY) for a given pool.

    Parameters:
    - pool: The pool object containing the supply rate method.
    - balance: The total balance available for investment.
    - pools: A list of pools to be considered for the calculation.

    Returns:
    - APY as a int.
    """
    if not pools:
        raise ValueError("The pools dict cannot be empty.")
    
    match pool.pool_type:
        case POOL_TYPES.DAI_SAVINGS:
            apy = pool.supply_rate()
        case _:
            apy = pool.supply_rate(balance // len(pools))
    
    return apy