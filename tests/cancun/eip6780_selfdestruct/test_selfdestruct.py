"""
abstract: Tests [EIP-6780: SELFDESTRUCT only in same transaction](https://eips.ethereum.org/EIPS/eip-6780)

    Tests for [EIP-6780: SELFDESTRUCT only in same transaction](https://eips.ethereum.org/EIPS/eip-6780).

"""  # noqa: E501

from itertools import count
from typing import Dict, List

import pytest

from ethereum_test_tools import (
    Account,
    Block,
    BlockchainTestFiller,
    Environment,
    Initcode,
    StateTestFiller,
    Storage,
    TestAddress,
    Transaction,
    compute_create2_address,
    compute_create_address,
    to_address,
    to_hash_bytes,
)
from ethereum_test_tools.vm.opcode import Opcodes as Op

REFERENCE_SPEC_GIT_PATH = "EIPS/eip-6780.md"
REFERENCE_SPEC_VERSION = "2f8299df31bb8173618901a03a8366a3183479b0"

SELFDESTRUCT_EIP_NUMBER = 6780


# TODO:

# Destroy and re-create multiple times in the same tx
# Create and destroy multiple contracts in the same tx
# Create multiple contracts in a tx and destroy only one of them, but attempt to destroy the other one in a subsequent tx
# Create contract, attempt to destroy it in a subsequent tx in the same block
# Create a contract and try to destroy using another calltype (e.g. Delegatecall then destroy)
# Create a contract that creates another contract, then selfdestruct only the parent (or vice versa)
# Test selfdestructing using all types of create
# Create a contract using CREATE2, then in a subsequent tx do CREATE2 to the same address, and try to self-destruct
# Create a contract using CREATE2 to an account that has some balance, and try to self-destruct it in the same tx


@pytest.fixture
def env() -> Environment:
    """Default environment for all tests."""
    return Environment(
        coinbase="0x2adc25665018aa1fe0e6bc666dac8fc2697ff9ba",
        gas_limit=10000000000,
    )


@pytest.fixture
def sendall_destination_address() -> int:
    """Account that receives the balance from the self-destructed account."""
    return 0x1234


@pytest.fixture
def selfdestruct_code(
    sendall_destination_address: int,
) -> bytes:
    """Bytecode that self-destructs."""
    return Op.SSTORE(
        0,
        Op.ADD(Op.SLOAD(0), 1),  # Add to the SSTORE'd value each time we enter the contract
    ) + Op.SELFDESTRUCT(sendall_destination_address)


@pytest.fixture
def selfdestruct_contract_initcode(
    selfdestruct_code: bytes,
) -> bytes:
    """Prepares an initcode that creates a self-destructing account."""
    return Initcode(deploy_code=selfdestruct_code).assemble()


@pytest.mark.parametrize("create_opcode", [Op.CREATE, Op.CREATE2])
@pytest.mark.parametrize("call_times", [1, 40])
@pytest.mark.parametrize("prefunded_selfdestruct_address", [False, True])
@pytest.mark.parametrize(
    "eips",
    [
        [SELFDESTRUCT_EIP_NUMBER],
        [],
    ],
    ids=["eip-enabled", "eip-disabled"],
)
@pytest.mark.valid_from("Shanghai")
def test_create_selfdestruct_same_tx(
    state_test: StateTestFiller,
    eips: List[int],
    env: Environment,
    selfdestruct_contract_initcode: bytes,
    sendall_destination_address: int,
    create_opcode: Op,
    call_times: int,  # Number of times to call the self-destructing contract in the same tx
    prefunded_selfdestruct_address: bool,
):
    """
    TODO test that if a contract is created and then selfdestructs in the same
    transaction the contract should not be created.
    """
    eip_enabled = SELFDESTRUCT_EIP_NUMBER in eips

    # Our entry point is an initcode that in turn creates a self-destructing contract
    entry_code_address = compute_create_address(TestAddress, 0)

    # Address of a pre-existing contract we use to simply copy initcode from
    selfdestruct_code_address = to_address(0x200)

    # Calculate the address of the selfdestructing contract
    if create_opcode == Op.CREATE:
        selfdestruct_contract_address = compute_create_address(entry_code_address, 1)
    elif create_opcode == Op.CREATE2:
        selfdestruct_contract_address = compute_create2_address(
            entry_code_address, 0, selfdestruct_contract_initcode
        )
    else:
        raise Exception("Invalid opcode")

    # Bytecode used to create the contract, can be CREATE or CREATE2
    op_args = [
        0,  # Value
        0,  # Offset
        len(selfdestruct_contract_initcode),  # Length
    ]
    if create_opcode == Op.CREATE2:
        # CREATE2 requires a salt argument
        op_args.append(0)
    create_bytecode = create_opcode(*op_args)

    # Entry code that will be executed, creates the contract and then calls it in the same tx
    entry_code = (
        # Initcode is already deployed at `selfdestruct_code_address`, so just copy it
        Op.EXTCODECOPY(
            Op.PUSH20(selfdestruct_code_address),
            0,
            0,
            len(selfdestruct_contract_initcode),
        )
        # And we store the created address for verification purposes
        + Op.SSTORE(
            0,
            create_bytecode,
        )
    )

    sendall_amount = 0

    # Call the self-destructing contract multiple times as required, incrementing the wei sent each
    # time
    for i in range(call_times):
        entry_code += Op.CALL(
            Op.GASLIMIT,  # Gas
            Op.PUSH20(selfdestruct_contract_address),  # Address
            i,  # Value
            0,
            0,
            0,
            0,
        )

    # Lastly return "0x00" so the entry point contract is created and we can retain the stored
    # values for verification.
    entry_code += Op.RETURN(len(selfdestruct_contract_initcode), 1)

    pre = {
        TestAddress: Account(balance=1000000000000000000000),
        selfdestruct_code_address: Account(code=selfdestruct_contract_initcode),
    }
    if prefunded_selfdestruct_address:
        pre[selfdestruct_contract_address] = Account(balance=1000000000000000000000)
        sendall_amount += 1000000000000000000000

    post: Dict[str, Account] = {
        entry_code_address: Account(code="0x00", storage={0: selfdestruct_contract_address}),
        selfdestruct_contract_address: Account.NONEXISTENT,  # type: ignore
        selfdestruct_code_address: Account(code=selfdestruct_contract_initcode),
    }
    if sendall_amount > 0:
        post[to_address(sendall_destination_address)] = Account(balance=sendall_amount)

    nonce = count()
    tx = Transaction(
        ty=0x0,
        data=entry_code,
        chain_id=0x0,
        nonce=next(nonce),
        to=None,
        gas_limit=100000000,
        gas_price=10,
        protected=False,
    )

    state_test(env=env, pre=pre, post=post, txs=[tx])


@pytest.mark.parametrize("create_opcode", [Op.CREATE2])  # Can only recreate using CREATE2
@pytest.mark.parametrize("recreate_times", [1])
@pytest.mark.parametrize("call_times", [1])
@pytest.mark.parametrize(
    "eips",
    [
        [SELFDESTRUCT_EIP_NUMBER],
        [],
    ],
    ids=["eip-enabled", "eip-disabled"],
)
@pytest.mark.valid_from("Shanghai")
def test_recreate_selfdestructed_contract(
    blockchain_test: BlockchainTestFiller,
    env: Environment,
    selfdestruct_contract_initcode: bytes,
    create_opcode: Op,
    recreate_times: int,  # Number of times to recreate the contract in different transactions
    call_times: int,  # Number of times to call the self-destructing contract in the same tx
):
    """
    TODO test that if a contract is created and then selfdestructs in the same
    transaction the contract should not be created.
    """
    assert create_opcode == Op.CREATE2, "cannot recreate contract using CREATE opcode"

    entry_code_address = to_address(
        0x100
    )  # Needs to be constant to be able to recreate the contract
    selfdestruct_code_address = to_address(0x200)

    # Calculate the address of the selfdestructing contract
    if create_opcode == Op.CREATE:
        selfdestruct_contract_address = compute_create_address(entry_code_address, 1)
    elif create_opcode == Op.CREATE2:
        selfdestruct_contract_address = compute_create2_address(
            entry_code_address, 0, selfdestruct_contract_initcode
        )
    else:
        raise Exception("Invalid opcode")

    # Bytecode used to create the contract
    op_args = [0, 0, len(selfdestruct_contract_initcode)]
    if create_opcode == Op.CREATE2:
        op_args.append(0)

    create_bytecode = create_opcode(*op_args)

    # Entry code that will be executed, creates the contract and then calls it
    entry_code = (
        # Initcode is already deployed at selfdestruct_code_address, so just copy it
        Op.EXTCODECOPY(
            Op.PUSH20(selfdestruct_code_address),
            0,
            0,
            len(selfdestruct_contract_initcode),
        )
        + Op.SSTORE(
            Op.CALLDATALOAD(0),
            create_bytecode,
        )
    )

    for i in range(call_times):
        entry_code += Op.CALL(
            Op.GASLIMIT,
            Op.PUSH20(selfdestruct_contract_address),
            i,
            0,
            0,
            0,
            0,
        )

    entry_code += Op.RETURN(len(selfdestruct_contract_initcode), 1)

    pre = {
        TestAddress: Account(balance=1000000000000000000000),
        entry_code_address: Account(code=entry_code),
        selfdestruct_code_address: Account(code=selfdestruct_contract_initcode),
    }

    entry_code_storage: Storage.StorageDictType = {}

    txs: List[Transaction] = []
    nonce = count()
    for i in range(recreate_times + 1):
        txs.append(
            Transaction(
                ty=0x0,
                data=to_hash_bytes(i),
                chain_id=0x0,
                nonce=next(nonce),
                to=entry_code_address,
                gas_limit=100000000,
                gas_price=10,
                protected=False,
            )
        )
        entry_code_storage[i] = selfdestruct_contract_address

    entry_address_expected_code = entry_code

    post: Dict[str, Account] = {
        entry_code_address: Account(code=entry_address_expected_code, storage=entry_code_storage),
        selfdestruct_contract_address: Account.NONEXISTENT,  # type: ignore
        selfdestruct_code_address: Account(code=selfdestruct_contract_initcode),
    }

    blockchain_test(genesis_environment=env, pre=pre, post=post, blocks=[Block(txs=txs)])


@pytest.mark.parametrize("selfdestruct_contract_initial_balance", [0, 1])
@pytest.mark.parametrize(
    "eips",
    [
        [SELFDESTRUCT_EIP_NUMBER],
        [],
    ],
    ids=["eip-enabled", "eip-disabled"],
)
@pytest.mark.valid_from("Shanghai")
def test_selfdestruct_prev_created(
    state_test: StateTestFiller,
    eips: List[int],
    env: Environment,
    selfdestruct_contract_initial_balance: int,
):
    """
    Test that if a previously created account that contains a selfdestruct is
    called, its balance is sent to the zero address.
    """
    eip_enabled = SELFDESTRUCT_EIP_NUMBER in eips

    # original code: 0x60016000556000ff  # noqa: SC100
    balance_destination_address = 0x200
    code = Op.SSTORE(0, 1) + Op.SELFDESTRUCT(balance_destination_address)

    selfdestruct_contract_address = "0x1111111111111111111111111111111111111111"
    pre = {
        TestAddress: Account(balance=1000000000000000000000),
        selfdestruct_contract_address: Account(
            balance=selfdestruct_contract_initial_balance, code=code
        ),
        to_address(balance_destination_address): Account(balance=1),
    }

    post: Dict[str, Account] = {
        to_address(balance_destination_address): Account(
            balance=selfdestruct_contract_initial_balance + 1
        ),
    }

    if eip_enabled:
        post[selfdestruct_contract_address] = Account(balance=0, code=code)
    else:
        post[selfdestruct_contract_address] = Account.NONEXISTENT  # type: ignore

    tx = Transaction(
        ty=0x0,
        data="",
        chain_id=0x0,
        nonce=0,
        to=selfdestruct_contract_address,
        gas_limit=100000000,
        gas_price=10,
        protected=False,
    )

    state_test(env=env, pre=pre, post=post, txs=[tx])


@pytest.mark.parametrize(
    "selfdestruct_contract_initial_balance,prefunding_tx",
    [
        (0, False),
        (1, False),
        (1, True),
    ],
    ids=[
        "no-prefund",
        "prefunded-in-selfdestruct-tx",
        "prefunded-in-tx-before-create-tx",
    ],
)
@pytest.mark.parametrize(
    "eips",
    [
        [SELFDESTRUCT_EIP_NUMBER],
        [],
    ],
    ids=["eip-enabled", "eip-disabled"],
)
@pytest.mark.valid_from("Shanghai")
def test_selfdestruct_prev_created_same_block(
    blockchain_test: BlockchainTestFiller,
    eips: List[int],
    env: Environment,
    selfdestruct_code: bytes,
    prefunding_tx: bool,
    selfdestruct_contract_initcode: bytes,
    selfdestruct_contract_initial_balance: int,
    sendall_destination_address: int,
):
    """
    Test that if an account created in the same block that contains a selfdestruct is
    called, its balance is sent to the zero address.
    """
    eip_enabled = SELFDESTRUCT_EIP_NUMBER in eips

    selfdestruct_contract_address = compute_create_address(TestAddress, 1 if prefunding_tx else 0)

    pre = {
        TestAddress: Account(balance=1000000000000000000000),
        to_address(sendall_destination_address): Account(balance=1),
    }

    post: Dict[str, Account] = {
        to_address(sendall_destination_address): Account(
            balance=selfdestruct_contract_initial_balance + 1
        ),
    }

    if eip_enabled:
        post[selfdestruct_contract_address] = Account(balance=0, code=selfdestruct_code)
    else:
        post[selfdestruct_contract_address] = Account.NONEXISTENT  # type: ignore

    txs: List[Transaction] = []
    nonce = count()
    if prefunding_tx:
        # Send some balance to the account to be created before it is created
        txs += [
            Transaction(
                ty=0x0,
                chain_id=0x0,
                value=selfdestruct_contract_initial_balance,
                nonce=next(nonce),
                to=selfdestruct_contract_address,
                gas_limit=100000000,
                gas_price=10,
                protected=False,
            )
        ]
    txs += [
        Transaction(
            ty=0x0,
            data=selfdestruct_contract_initcode,
            chain_id=0x0,
            value=0,
            nonce=next(nonce),
            to=None,
            gas_limit=100000000,
            gas_price=10,
            protected=False,
        ),
        Transaction(
            ty=0x0,
            chain_id=0x0,
            value=selfdestruct_contract_initial_balance if not prefunding_tx else 0,
            nonce=next(nonce),
            to=selfdestruct_contract_address,
            gas_limit=100000000,
            gas_price=10,
            protected=False,
        ),
    ]

    blockchain_test(genesis_environment=env, pre=pre, post=post, blocks=[Block(txs=txs)])
