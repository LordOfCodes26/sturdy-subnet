import os
import unittest

import gmpy2
import numpy as np
from dotenv import load_dotenv
from web3 import Web3
from web3.constants import ADDRESS_ZERO

from sturdy.algo import naive_algorithm
from sturdy.pool_registry.pool_registry import POOL_REGISTRY
from sturdy.pools import *
from sturdy.protocol import REQUEST_TYPES, AllocateAssets
from sturdy.validator.reward import (
    adjust_rewards_for_plagiarism,
    annualized_yield_pct,
    calculate_penalties,
    calculate_rewards_with_adjusted_penalties,
    format_allocations,
    get_allocation_similarity_matrix,
    get_apy_similarity_matrix,
    get_distance,
    normalize_exp,
)

load_dotenv()
WEB3_PROVIDER_URL = os.getenv("WEB3_PROVIDER_URL")

BEEF = "0xDeaDbeefdEAdbeefdEadbEEFdeadbeEFdEaDbeeF"


class TestGetDistance(unittest.TestCase):
    def test_identical_allocations(self) -> None:
        # Test case where allocations are identical, expecting 0 distance
        alloc_a = np.array([100, 200, 300], dtype=object)
        alloc_b = np.array([100, 200, 300], dtype=object)
        total_assets = 600
        self.assertEqual(get_distance(alloc_a, alloc_b, total_assets), 0.0)

    def test_positive_allocations(self) -> None:
        # Test case with positive values, expecting a non-zero distance
        alloc_a = np.array([100, 200, 300], dtype=object)
        alloc_b = np.array([50, 150, 250], dtype=object)
        total_assets = 600
        expected_distance = gmpy2.sqrt(sum((x - y) ** 2 for x, y in zip(alloc_a, alloc_b, strict=False))) / gmpy2.sqrt(
            float(2 * total_assets**2)
        )
        self.assertAlmostEqual(float(get_distance(alloc_a, alloc_b, total_assets)), float(expected_distance), places=6)

    def test_zero_allocations(self) -> None:
        # Test case where one allocation is all zeros
        alloc_a = np.array([100, 200, 300], dtype=object)
        alloc_b = np.array([0, 0, 0], dtype=object)
        total_assets = 600
        expected_distance = gmpy2.sqrt(sum(x**2 for x in alloc_a)) / gmpy2.sqrt(float(2 * total_assets**2))
        self.assertAlmostEqual(float(get_distance(alloc_a, alloc_b, total_assets)), float(expected_distance), places=6)

    def test_large_numbers(self) -> None:
        # Test case with very large numbers to ensure precision
        alloc_a = np.array([2**100, 2**100, 2**100], dtype=object)
        alloc_b = np.array([2**99, 2**99, 2**99], dtype=object)
        total_assets = 2**100
        expected_distance = gmpy2.sqrt(sum((x - y) ** 2 for x, y in zip(alloc_a, alloc_b, strict=False))) / gmpy2.sqrt(
            float(2 * total_assets**2)
        )
        self.assertAlmostEqual(float(get_distance(alloc_a, alloc_b, total_assets)), float(expected_distance), places=6)

    def test_large_numbers_gap(self) -> None:
        # Test case with very large numbers to ensure precision
        alloc_a = np.array([1e100, 1e100, 1e100], dtype=object)
        alloc_b = np.array([1e21, 1e21, 1e21], dtype=object)
        total_assets = 1e101
        expected_distance = gmpy2.sqrt(sum((x - y) ** 2 for x, y in zip(alloc_a, alloc_b, strict=False))) / gmpy2.sqrt(
            float(2 * total_assets**2)
        )
        self.assertAlmostEqual(float(get_distance(alloc_a, alloc_b, total_assets)), float(expected_distance), places=6)

    def test_different_lengths(self) -> None:
        # Test case with differing lengths should raise an error, and return 69.0
        alloc_a = np.array([100, 200], dtype=object)
        alloc_b = np.array([100, 200, 300], dtype=object)
        total_assets = 600
        self.assertEqual(69.0, get_distance(alloc_a, alloc_b, total_assets))


class TestDynamicNormalizeZScore(unittest.TestCase):
    def test_basic_normalization(self) -> None:
        # Test a simple AllocationsDict with large values
        apys_and_allocations = {"1": {"apy": 1e16}, "2": {"apy": 2e16}, "3": {"apy": 3e16}, "4": {"apy": 4e16}}
        normalized = normalize_exp(apys_and_allocations)

        # Check if output is normalized between 0 and 1
        self.assertAlmostEqual(normalized.min().item(), 0.0, places=5)
        self.assertAlmostEqual(normalized.max().item(), 1.0, places=5)

    def test_with_low_outliers(self) -> None:
        # Test with low outliers in AllocationsDict
        apys_and_allocations = {
            "1": {"apy": 1e16},
            "2": {"apy": 1e16},
            "3": {"apy": 1e16},
            "4": {"apy": 5e16},
            "5": {"apy": 1e17},
        }
        normalized = normalize_exp(apys_and_allocations)

        # Check that outliers don't affect the overall normalization
        self.assertAlmostEqual(normalized.min().item(), 0.0, places=5)
        self.assertAlmostEqual(normalized.max().item(), 1.0, places=5)

    def test_with_high_outliers(self) -> None:
        # Test with high outliers in AllocationsDict
        apys_and_allocations = {
            "1": {"apy": 5e16},
            "2": {"apy": 6e16},
            "3": {"apy": 7e16},
            "4": {"apy": 1e17},
            "5": {"apy": 2e17},
        }
        normalized = normalize_exp(apys_and_allocations)

        # Check that the function correctly handles high outliers
        self.assertAlmostEqual(normalized.min().item(), 0.0, places=5)
        self.assertAlmostEqual(normalized.max().item(), 1.0, places=5)

    def test_uniform_values(self) -> None:
        # Test where all values are the same
        apys_and_allocations = {"1": {"apy": 1e16}, "2": {"apy": 1e16}, "3": {"apy": 1e16}, "4": {"apy": 1e16}}
        normalized = normalize_exp(apys_and_allocations)

        # If all values are the same, the output should also be uniform (or handle gracefully)
        self.assertTrue(
            np.allclose(normalized, np.zeros_like(np.array([v["apy"] for v in apys_and_allocations.values()])), atol=1e-8)
        )

    def test_low_variance(self) -> None:
        # Test with low variance data (values are close to each other)
        apys_and_allocations = {
            "1": {"apy": 1e16},
            "2": {"apy": 1.01e16},
            "3": {"apy": 1.02e16},
            "4": {"apy": 1.03e16},
            "5": {"apy": 1.04e16},
        }
        normalized = normalize_exp(apys_and_allocations)

        # Check if normalization happens correctly
        self.assertAlmostEqual(normalized.min().item(), 0.0, places=5)
        self.assertAlmostEqual(normalized.max().item(), 1.0, places=5)

    def test_high_variance(self) -> None:
        # Test with high variance data
        apys_and_allocations = {"1": {"apy": 1e16}, "2": {"apy": 1e17}, "3": {"apy": 5e17}, "4": {"apy": 1e18}}
        normalized = normalize_exp(apys_and_allocations)

        # Ensure that the normalization works even with high variance
        self.assertAlmostEqual(normalized.min().item(), 0.0, places=5)
        self.assertAlmostEqual(normalized.max().item(), 1.0, places=5)


class TestRewardFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # runs tests on local mainnet fork at block: 20233401
        cls.w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
        assert cls.w3.is_connected()

        class EmptyVali:
            pass

        cls.vali = EmptyVali()

        cls.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 20976304,
                    },
                },
            ],
        )

        cls.snapshot_id = cls.w3.provider.make_request("evm_snapshot", [])  # type: ignore[]

    def tearDown(self) -> None:
        # Optional: Revert to the original snapshot after each test
        self.w3.provider.make_request("evm_revert", self.snapshot_id)  # type: ignore[]

    def test_check_allocations_sturdy(self) -> None:
        A = "0x6311fF24fb15310eD3d2180D3d0507A21a8e5227"
        VAULT = "0x73E4C11B670Ef9C025A030A20b72CB9150E54523"
        # assuming block # is: 20233401
        allocations = {A: int(6e23)}
        assets_and_pools = {
            "total_assets": int(100e23),
            "pools": {
                A: VariableInterestSturdySiloStrategy(
                    user_address=VAULT,
                    contract_address=A,
                ),
            },
        }

        pool_a: VariableInterestSturdySiloStrategy = assets_and_pools["pools"][A]
        pool_a.sync(web3_provider=self.w3)

        # case: borrow_amount <= assets_available, deposit_amount < assets_available
        pool_a._total_supplied_assets = int(100e23)
        pool_a._totalBorrow = int(10e23)
        pool_a._user_deposits = int(5e23)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount >= assets_available
        pool_a._totalBorrow = int(97e23)
        pool_a._user_deposits = int(5e23)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations)
        self.assertFalse(result)

        # should return True
        pool_a._totalBorrow = int(97e23)
        pool_a._user_deposits = int(5e23)
        allocations[A] = int(4e23)

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount < assets_available
        pool_a._totalBorrow = int(10e23)
        pool_a._user_deposits = int(1e23)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

    def test_check_allocations_aave(self) -> None:
        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 20233401,
                    },
                },
            ],
        )

        # aUSDC (Aave USDC)
        A = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
        # assuming block # is: 20233401
        allocations = {A: int(1e17)}
        assets_and_pools = {
            "total_assets": int(200e18),
            "pools": {
                A: AaveV3DefaultInterestRateV2Pool(
                    user_address=ADDRESS_ZERO,
                    contract_address=A,
                ),
            },
        }

        pool_a: AaveV3DefaultInterestRateV2Pool = assets_and_pools["pools"][A]
        pool_a.sync(self.w3)

        # case: borrow_amount <= assets_available, deposit_amount < assets_available
        pool_a._total_supplied_assets = int(100e6)
        pool_a._nextTotalStableDebt = 0
        pool_a._totalVariableDebt = int(10e6)
        pool_a._user_deposits = int(5e18)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount >= assets_available
        pool_a._nextTotalStableDebt = 0
        pool_a._totalVariableDebt = int(97e6)
        pool_a._user_deposits = int(5e18)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertFalse(result)

        # should return True
        pool_a._nextTotalStableDebt = 0
        pool_a._totalVariableDebt = int(97e6)
        pool_a._user_deposits = int(5e18)
        allocations[A] = int(4e18)

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount < assets_available
        pool_a._nextTotalStableDebt = 0
        pool_a._totalVariableDebt = int(97e6)
        pool_a._user_deposits = int(1e18)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

    def test_check_allocations_compound(self) -> None:
        A = "0xc3d688B66703497DAA19211EEdff47f25384cdc3"
        # assuming block # is: 20233401
        allocations = {A: int(5e26)}
        assets_and_pools = {
            "total_assets": int(100e26),
            "pools": {
                A: CompoundV3Pool(
                    user_address=ADDRESS_ZERO,
                    contract_address=A,
                ),
            },
        }

        pool_a: CompoundV3Pool = assets_and_pools["pools"][A]
        pool_a.sync(self.w3)

        # case: borrow_amount <= assets_available, deposit_amount < assets_available
        pool_a._total_supplied_assets = int(100e14)
        pool_a._total_borrow = int(10e14)
        pool_a._user_deposits = int(5e14)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount >= assets_available
        pool_a._total_borrow = int(97e14)
        pool_a._user_deposits = int(5e14)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertFalse(result)

        # should return True
        pool_a._total_borrow = int(97e14)
        pool_a._user_deposits = int(5e14)
        allocations[A] = int(4e26)

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount < assets_available
        pool_a._total_borrow = int(97e14)
        pool_a._user_deposits = int(1e14)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

    def test_check_allocations_morpho(self) -> None:
        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 20874859,
                    },
                },
            ],
        )

        A = "0xd63070114470f685b75B74D60EEc7c1113d33a3D"
        # assuming block # is: 20233401
        allocations = {A: 0}
        assets_and_pools = {
            "total_assets": int(200e14),
            "pools": {
                A: MorphoVault(
                    user_address=ADDRESS_ZERO,
                    contract_address=A,
                ),
            },
        }

        pool_a: MorphoVault = assets_and_pools["pools"][A]
        pool_a.sync(self.w3)

        # case: borrow_amount <= assets_available, deposit_amount < assets_available
        pool_a._total_supplied_assets = int(100e14)
        pool_a._curr_borrows = int(10e14)
        pool_a._user_deposits = int(5e14)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount >= assets_available
        pool_a._curr_borrows = int(97e14)
        pool_a._user_deposits = int(5e14)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertFalse(result)

        # should return True
        pool_a._curr_borrows = int(97e14)
        pool_a._user_deposits = int(5e14)
        allocations[A] = int(4e14)

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: borrow_amount > assets_available, deposit_amount < assets_available
        pool_a._curr_borrows = int(97e14)
        pool_a._user_deposits = int(1e14)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

    def test_check_allocations_yearn(self) -> None:
        A = "0xBe53A109B494E5c9f97b9Cd39Fe969BE68BF6204"
        # assuming block # is: 20233401
        allocations = {A: 0}
        assets_and_pools = {
            "total_assets": int(1e12),
            "pools": {
                A: YearnV3Vault(
                    user_address=ADDRESS_ZERO,
                    contract_address=A,
                ),
            },
        }

        pool_a: MorphoVault = assets_and_pools["pools"][A]
        pool_a.sync(self.w3)

        # case: max withdraw = deposit amount
        pool_a._max_withdraw = int(1e9)
        pool_a._user_deposits = int(1e9)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

        # case: max withdraw = 0
        pool_a._max_withdraw = 0
        pool_a._user_deposits = int(1e9)
        allocations[A] = 1

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertFalse(result)

        # should return True
        pool_a._max_withdraw = int(1e9)
        pool_a._user_deposits = int(5e9)
        allocations[A] = int(4e9)

        result = check_allocations(assets_and_pools, allocations, alloc_threshold=0)
        self.assertTrue(result)

    def test_format_allocations(self) -> None:
        allocations = {"1": int(5e18), "2": int(3e18)}
        assets_and_pools = {
            "pools": {
                "1": {"reserve_size": 1000},
                "2": {"reserve_size": 1000},
                "3": {"reserve_size": 1000},
            }
        }

        expected_output = {"1": 5e18, "2": 3e18, "3": 0}
        result = format_allocations(allocations, assets_and_pools)

        self.assertEqual(result, expected_output)

    def test_format_allocations_no_pools(self) -> None:
        allocations = {"1": int(5e18), "2": int(3e18)}
        assets_and_pools = {"pools": {}}

        expected_output = {"1": 5e18, "2": 3e18}
        result = format_allocations(allocations, assets_and_pools)

        self.assertEqual(result, expected_output)

    def test_format_allocations_empty(self) -> None:
        allocations = {}
        assets_and_pools = {
            "pools": {
                "1": {"reserve_size": 1000},
                "2": {"reserve_size": 1000},
            }
        }

        expected_output = {"1": 0, "2": 0}
        result = format_allocations(allocations, assets_and_pools)

        self.assertEqual(result, expected_output)

    def test_get_allocation_similarity_matrix(self) -> None:
        apys_and_allocations = {
            "miner_1": {
                "apy": int(0.05e18),
                "allocations": {"pool_1": 30e18, "pool_2": 20e18},
            },
            "miner_2": {
                "apy": int(0.04e18),
                "allocations": {"pool_1": 40e18, "pool_2": 10e18},
            },
            "miner_3": {
                "apy": int(0.06e18),
                "allocations": {"pool_1": 30e18, "pool_2": 20e18},
            },
        }
        assets_and_pools = {
            "pools": {
                "pool_1": {"reserve_size": 100e18},
                "pool_2": {"reserve_size": 100e18},
            },
            "total_assets": 10e18,
        }

        total_assets = assets_and_pools["total_assets"]

        expected_similarity_matrix = {
            "miner_2": {
                "miner_1": get_distance(
                    np.array([gmpy2.mpz(40e18), gmpy2.mpz(10e18)], dtype=object),
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    total_assets,
                ),
                "miner_3": get_distance(
                    np.array([gmpy2.mpz(40e18), gmpy2.mpz(10e18)], dtype=object),
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    total_assets,
                ),
            },
            "miner_1": {
                "miner_2": get_distance(
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    np.array([gmpy2.mpz(40e18), gmpy2.mpz(10e18)], dtype=object),
                    total_assets,
                ),
                "miner_3": get_distance(
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    total_assets,
                ),
            },
            "miner_3": {
                "miner_1": get_distance(
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    total_assets,
                ),
                "miner_2": get_distance(
                    np.array([gmpy2.mpz(30e18), gmpy2.mpz(20e18)], dtype=object),
                    np.array([gmpy2.mpz(40e18), gmpy2.mpz(10e18)], dtype=object),
                    total_assets,
                ),
            },
        }

        result = get_allocation_similarity_matrix(apys_and_allocations, assets_and_pools)

        for miner_a in expected_similarity_matrix:
            for miner_b in expected_similarity_matrix[miner_a]:
                self.assertAlmostEqual(
                    result[miner_a][miner_b],
                    expected_similarity_matrix[miner_a][miner_b],
                    places=5,
                )

    def test_get_apy_similarity_matrix(self) -> None:
        apys_and_allocations = {
            "miner_1": {
                "apy": int(0.05e18),
                "allocations": {"pool_1": 30e18, "pool_2": 20e18},
            },
            "miner_2": {
                "apy": int(0.04e18),
                "allocations": {"pool_1": 40e18, "pool_2": 10e18},
            },
            "miner_3": {
                "apy": int(0.06e18),
                "allocations": {"pool_1": 30e18, "pool_2": 20e18},
            },
        }

        expected_similarity_matrix = {
            "miner_1": {
                "miner_2": get_distance(
                    np.array([gmpy2.mpz(0.05e18)], dtype=object),
                    np.array([gmpy2.mpz(0.04e18)], dtype=object),
                    gmpy2.mpz(0.05e18),
                ),
                "miner_3": get_distance(
                    np.array([gmpy2.mpz(0.05e18)], dtype=object),
                    np.array([gmpy2.mpz(0.06e18)], dtype=object),
                    gmpy2.mpz(0.06e18),
                ),
            },
            "miner_2": {
                "miner_1": get_distance(
                    np.array([gmpy2.mpz(0.04e18)], dtype=object),
                    np.array([gmpy2.mpz(0.05e18)], dtype=object),
                    gmpy2.mpz(0.05e18),
                ),
                "miner_3": get_distance(
                    np.array([gmpy2.mpz(0.04e18)], dtype=object),
                    np.array([gmpy2.mpz(0.06e18)], dtype=object),
                    gmpy2.mpz(0.06e18),
                ),
            },
            "miner_3": {
                "miner_1": get_distance(
                    np.array([gmpy2.mpz(0.06e18)], dtype=object),
                    np.array([gmpy2.mpz(0.05e18)], dtype=object),
                    gmpy2.mpz(0.06e18),
                ),
                "miner_2": get_distance(
                    np.array([gmpy2.mpz(0.06e18)], dtype=object),
                    np.array([gmpy2.mpz(0.04e18)], dtype=object),
                    gmpy2.mpz(0.06e18),
                ),
            },
        }

        result = get_apy_similarity_matrix(apys_and_allocations)

        for miner_a in expected_similarity_matrix:
            for miner_b in expected_similarity_matrix[miner_a]:
                self.assertAlmostEqual(
                    result[miner_a][miner_b],
                    expected_similarity_matrix[miner_a][miner_b],
                    places=5,
                )

    def test_get_allocation_similarity_matrix_empty(self) -> None:
        apys_and_allocations = {
            "miner_1": {
                "apy": int(0.05e18),
                "allocations": {"pool_1": 30, "pool_2": 20},
            },
            "miner_2": {
                "apy": int(0.04e18),
                "allocations": {"pool_1": 40, "pool_2": 10},
            },
            "miner_3": {"apy": 0, "allocations": None},
        }
        assets_and_pools = {
            "pools": {
                "pool_1": {"reserve_size": 100},
                "pool_2": {"reserve_size": 100},
            },
            "total_assets": 100,
        }

        total_assets = assets_and_pools["total_assets"]

        expected_similarity_matrix = {
            "miner_1": {
                "miner_2": get_distance(
                    np.array([gmpy2.mpz(30), gmpy2.mpz(20)], dtype=object),
                    np.array([gmpy2.mpz(40), gmpy2.mpz(10)], dtype=object),
                    total_assets,
                ),
                "miner_3": float("inf"),
            },
            "miner_2": {
                "miner_1": get_distance(
                    np.array([gmpy2.mpz(40), gmpy2.mpz(10)], dtype=object),
                    np.array([gmpy2.mpz(30), gmpy2.mpz(20)], dtype=object),
                    total_assets,
                ),
                "miner_3": float("inf"),
            },
            "miner_3": {"miner_1": float("inf"), "miner_2": float("inf")},
        }

        result = get_allocation_similarity_matrix(apys_and_allocations, assets_and_pools)

        for miner_a in expected_similarity_matrix:
            for miner_b in expected_similarity_matrix[miner_a]:
                self.assertAlmostEqual(
                    result[miner_a][miner_b],
                    expected_similarity_matrix[miner_a][miner_b],
                    places=5,
                )

    def test_get_apy_similarity_matrix_empty(self) -> None:
        apys_and_allocations = {
            "miner_1": {
                "apy": int(0.05e18),
                "allocations": {"pool_1": 30, "pool_2": 20},
            },
            "miner_2": {
                "apy": int(0.04e18),
                "allocations": {"pool_1": 40, "pool_2": 10},
            },
            "miner_3": {"apy": 0, "allocations": None},
        }

        expected_similarity_matrix = {
            "miner_1": {
                "miner_2": get_distance(
                    np.array([gmpy2.mpz(0.05e18)], dtype=object),
                    np.array([gmpy2.mpz(0.04e18)], dtype=object),
                    gmpy2.mpz(0.05e18),
                ),
                "miner_3": get_distance(
                    np.array([gmpy2.mpz(0.05e18)], dtype=object), np.array([gmpy2.mpz(0)], dtype=object), gmpy2.mpz(0.05e18)
                ),
            },
            "miner_2": {
                "miner_1": get_distance(
                    np.array([gmpy2.mpz(0.04e18)], dtype=object),
                    np.array([gmpy2.mpz(0.05e18)], dtype=object),
                    gmpy2.mpz(0.05e18),
                ),
                "miner_3": get_distance(
                    np.array([gmpy2.mpz(0.04e18)], dtype=object), np.array([gmpy2.mpz(0)], dtype=object), gmpy2.mpz(0.04e18)
                ),
            },
            "miner_3": {
                "miner_1": get_distance(
                    np.array([gmpy2.mpz(0)], dtype=object), np.array([gmpy2.mpz(0.05e18)], dtype=object), gmpy2.mpz(0.05e18)
                ),
                "miner_2": get_distance(
                    np.array([gmpy2.mpz(0)], dtype=object), np.array([gmpy2.mpz(0.04e18)], dtype=object), gmpy2.mpz(0.04e18)
                ),
            },
        }

        result = get_apy_similarity_matrix(apys_and_allocations)

        for miner_a in expected_similarity_matrix:
            for miner_b in expected_similarity_matrix[miner_a]:
                self.assertAlmostEqual(
                    result[miner_a][miner_b],
                    expected_similarity_matrix[miner_a][miner_b],
                    places=5,
                )

    def test_calculate_penalties(self) -> None:
        allocation_similarity_matrix = {
            "1": {"2": 0.05, "3": 0.2},
            "2": {"1": 0.05, "3": 0.1},
            "3": {"1": 0.2, "2": 0.1},
        }
        apy_similarity_matrix = {
            "1": {"2": 0.05, "3": 0.2},
            "2": {"1": 0.05, "3": 0.1},
            "3": {"1": 0.2, "2": 0.1},
        }
        axon_times = {"1": 1.0, "2": 2.0, "3": 3.0}

        allocation_similarity_threshold = 0.2
        apy_similarity_threshold = 0.1

        expected_penalties = {"1": 0, "2": 1, "3": 1}
        result = calculate_penalties(
            allocation_similarity_matrix,
            apy_similarity_matrix,
            axon_times,
            allocation_similarity_threshold,
            apy_similarity_threshold,
        )

        self.assertEqual(result, expected_penalties)

    def test_calculate_penalties_no_apy_similarities(self) -> None:
        allocation_similarity_matrix = {
            "1": {"2": 0.05, "3": 0.2},
            "2": {"1": 0.05, "3": 0.1},
            "3": {"1": 0.2, "2": 0.1},
        }
        apy_similarity_matrix = {
            "1": {"2": 0.05, "3": 0.2},
            "2": {"1": 0.05, "3": 0.1},
            "3": {"1": 0.2, "2": 0.1},
        }
        axon_times = {"1": 1.0, "2": 2.0, "3": 3.0}
        allocation_similarity_threshold = 0.2
        apy_similarity_threshold = 0.05

        expected_penalties = {"1": 0, "2": 1, "3": 0}
        result = calculate_penalties(
            allocation_similarity_matrix,
            apy_similarity_matrix,
            axon_times,
            allocation_similarity_threshold,
            apy_similarity_threshold,
        )

        self.assertEqual(result, expected_penalties)

    def test_calculate_penalties_no_similarities(self) -> None:
        allocation_similarity_matrix = {
            "1": {"2": 0.5, "3": 0.6},
            "2": {"1": 0.5, "3": 0.7},
            "3": {"1": 0.6, "2": 0.7},
        }
        apy_similarity_matrix = {
            "1": {"2": 0.5, "3": 0.6},
            "2": {"1": 0.5, "3": 0.7},
            "3": {"1": 0.6, "2": 0.7},
        }
        axon_times = {"1": 1.0, "2": 2.0, "3": 3.0}

        allocation_similarity_threshold = 0.3
        apy_similarity_threshold = 0.1

        expected_penalties = {"1": 0, "2": 0, "3": 0}
        result = calculate_penalties(
            allocation_similarity_matrix,
            apy_similarity_matrix,
            axon_times,
            allocation_similarity_threshold,
            apy_similarity_threshold,
        )

        self.assertEqual(result, expected_penalties)

    def test_calculate_penalties_equal_times(self) -> None:
        allocation_similarity_matrix = {
            "1": {"2": 0.05, "3": 0.05},
            "2": {"1": 0.05, "3": 0.05},
            "3": {"1": 0.05, "2": 0.05},
        }
        apy_similarity_matrix = {
            "1": {"2": 0.05, "3": 0.05},
            "2": {"1": 0.05, "3": 0.05},
            "3": {"1": 0.05, "2": 0.05},
        }

        axon_times = {"1": 1.0, "2": 1.0, "3": 1.0}

        allocation_similarity_threshold = 0.1

        apy_similarity_threshold = 0.2

        expected_penalties = {"1": 2, "2": 2, "3": 2}

        result = calculate_penalties(
            allocation_similarity_matrix,
            apy_similarity_matrix,
            axon_times,
            allocation_similarity_threshold,
            apy_similarity_threshold,
        )

        self.assertEqual(result, expected_penalties)

    def test_calculate_rewards_with_adjusted_penalties(self) -> None:
        miners = ["1", "2", "3"]
        rewards_apy = np.array([1.0, 1.0, 1.0])
        penalties = {"1": 0, "2": 1, "3": 2}

        expected_rewards = np.array([1.0, 0.5, 0.0])
        result = calculate_rewards_with_adjusted_penalties(miners, rewards_apy, penalties)

        np.testing.assert_allclose(result, expected_rewards, rtol=0, atol=1e-5)

    def test_calculate_rewards_with_no_penalties(self) -> None:
        miners = ["1", "2", "3"]
        rewards_apy = np.array([0.05, 0.04, 0.03])
        penalties = {"1": 0, "2": 0, "3": 0}

        expected_rewards = np.array([0.05, 0.04, 0.03])
        result = calculate_rewards_with_adjusted_penalties(miners, rewards_apy, penalties)

        np.testing.assert_allclose(result, expected_rewards, rtol=0, atol=1e-5)

    def test_adjust_rewards_for_plagiarism(self) -> None:
        rewards_apy = np.array([0.05 / 0.05, 0.04 / 0.05, 0.03 / 0.05])
        apys_and_allocations = {
            "0": {"apy": 50, "allocations": {"asset_1": 200, "asset_2": 300}},  # APY: int
            "1": {"apy": 40, "allocations": {"asset_1": 202, "asset_2": 303}},
            "2": {"apy": 30, "allocations": {"asset_1": 200, "asset_2": 400}},
        }
        assets_and_pools = {
            "total_assets": 500,
            "pools": {"asset_1": 1000, "asset_2": 1000},
        }
        uids = ["0", "1", "2"]
        axon_times = {"0": 1.0, "1": 2.0, "2": 3.0}

        allocation_similarity_threshold = 0.1

        apy_similarity_threshold = 0.2

        expected_rewards = np.array([1.0, 0.0, 0.03 / 0.05])

        result = adjust_rewards_for_plagiarism(
            self.vali,
            rewards_apy,
            apys_and_allocations,
            assets_and_pools,
            uids,
            axon_times,
            allocation_similarity_threshold,
            apy_similarity_threshold,
        )

        np.testing.assert_array_almost_equal(result, expected_rewards, decimal=5)

    def test_adjust_rewards_for_one_plagiarism(self) -> None:
        rewards_apy = np.array([1.0, 1.0])
        apys_and_allocations = {
            "0": {"apy": 50, "allocations": {"asset_1": 200, "asset_2": 300}},
            "1": {"apy": 50, "allocations": {"asset_1": 200, "asset_2": 300}},
        }
        assets_and_pools = {
            "total_assets": 500,
            "pools": {"asset_1": 1000, "asset_2": 1000},
        }
        uids = ["0", "1"]
        axon_times = {"0": 1.0, "1": 2.0}

        expected_rewards = np.array([1.0, 0.0])

        allocation_similarity_threshold = 0.1
        apy_similarity_threshold = 0.2

        result = adjust_rewards_for_plagiarism(
            self.vali,
            rewards_apy,
            apys_and_allocations,
            assets_and_pools,
            uids,
            axon_times,
            allocation_similarity_threshold,
            apy_similarity_threshold,
        )

        np.testing.assert_array_almost_equal(result, expected_rewards, decimal=5)


class TestCalculateApy(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # runs tests on local mainnet fork at block: 20233401
        cls.w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
        assert cls.w3.is_connected()

        cls.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 21080765,
                    },
                },
            ],
        )

        cls.snapshot_id = cls.w3.provider.make_request("evm_snapshot", [])  # type: ignore[]

    def tearDown(self) -> None:
        # Optional: Revert to the original snapshot after each test
        self.w3.provider.make_request("evm_revert", self.snapshot_id)  # type: ignore[]

    def test_calculate_apy_sturdy(self) -> None:
        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 21075005,
                    },
                },
            ],
        )

        selected_entry = POOL_REGISTRY["Sturdy Crvusd Aggregator"]
        selected = assets_pools_for_challenge_data(selected_entry, self.w3)

        assets_and_pools = selected["assets_and_pools"]
        user_address = selected["user_address"]
        synapse = AllocateAssets(
            request_type=REQUEST_TYPES.SYNTHETIC,
            assets_and_pools=assets_and_pools,
            user_address=user_address,
        )

        allocations = naive_algorithm(self, synapse)

        extra_metadata = {}
        for contract_address, pool in assets_and_pools["pools"].items():
            pool.sync(self.w3)
            extra_metadata[contract_address] = pool._yield_index

        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 21080765,
                    },
                },
            ],
        )

        for pool in assets_and_pools["pools"].values():
            pool.sync(self.w3)

        apy = annualized_yield_pct(allocations, assets_and_pools, 604800, extra_metadata)
        print(f"annualized yield: {(float(apy) / 1e18) * 100}%")
        self.assertGreater(apy, 0)

    def test_calculate_apy_aave(self) -> None:
        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 21075005,
                    },
                },
            ],
        )

        # aave pools - with yearn strategies being their users
        selected_entry = {
            "assets_and_pools": {
                "pools": {
                    "0x018008bfb33d285247A21d44E50697654f754e63": {
                        "pool_type": "AAVE_DEFAULT",
                        "contract_address": "0x018008bfb33d285247A21d44E50697654f754e63",
                        "user_address": "0xF0825750791A4444c5E70743270DcfA8Bb38f959",
                    },
                    "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B": {
                        "pool_type": "AAVE_TARGET",
                        "contract_address": "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B",
                        "user_address": "0x1fd862499e9b9402de6c599b6c391f83981180ab",
                    },
                }
            }
        }

        selected = assets_pools_for_challenge_data(selected_entry, self.w3)

        assets_and_pools = selected["assets_and_pools"]
        synapse = AllocateAssets(
            request_type=REQUEST_TYPES.SYNTHETIC,
            assets_and_pools=assets_and_pools,
        )

        allocations = naive_algorithm(self, synapse)

        extra_metadata = {}
        for contract_address, pool in assets_and_pools["pools"].items():
            pool.sync(self.w3)
            extra_metadata[contract_address] = pool._yield_index

        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 21080765,
                    },
                },
            ],
        )

        for pool in assets_and_pools["pools"].values():
            pool.sync(self.w3)

        apy = annualized_yield_pct(allocations, assets_and_pools, 604800, extra_metadata)
        print(f"annualized yield: {(float(apy) / 1e18) * 100}%")
        self.assertGreater(apy, 0)

    def test_calculate_apy_morpho(self) -> None:
        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 21075005,
                    },
                },
            ],
        )

        selected_entry = POOL_REGISTRY["Morpho USDC Vaults"]
        selected = assets_pools_for_challenge_data(selected_entry, self.w3)

        assets_and_pools = selected["assets_and_pools"]
        user_address = selected["user_address"]
        synapse = AllocateAssets(
            request_type=REQUEST_TYPES.SYNTHETIC,
            assets_and_pools=assets_and_pools,
            user_address=user_address,
        )

        allocations = naive_algorithm(self, synapse)

        extra_metadata = {}
        for contract_address, pool in assets_and_pools["pools"].items():
            pool.sync(self.w3)
            extra_metadata[contract_address] = pool._yield_index

        self.w3.provider.make_request(
            "hardhat_reset",  # type: ignore[]
            [
                {
                    "forking": {
                        "jsonRpcUrl": WEB3_PROVIDER_URL,
                        "blockNumber": 21080765,
                    },
                },
            ],
        )

        for pool in assets_and_pools["pools"].values():
            pool.sync(self.w3)

        apy = annualized_yield_pct(allocations, assets_and_pools, 604800, extra_metadata)
        print(f"annualized yield: {(float(apy) / 1e18) * 100}%")
        self.assertGreater(apy, 0)


if __name__ == "__main__":
    unittest.main()
