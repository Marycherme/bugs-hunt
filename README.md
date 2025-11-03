# Bugs-Hunt: Cross-Chain Bridge Event Listener

This repository contains a Python-based simulation of a critical component in a cross-chain bridge architecture: the **Event Listener**. This service is designed to monitor a `Bridge` smart contract on a source blockchain, detect specific events (such as `TokensLocked`), and relay this information to an off-chain service responsible for completing the transaction on the destination chain.

## Concept

Cross-chain bridges allow users to transfer assets or data from one blockchain to another. A common architectural pattern is "Lock-and-Mint":

1.  **Lock**: A user sends tokens to a `Bridge` smart contract on the source chain (e.g., Ethereum). The contract locks these tokens and emits an event, such as `TokensLocked`, containing details of the transaction (recipient, amount, destination chain ID, etc.).
2.  **Listen**: Off-chain services, called listeners or relayers, constantly monitor the source chain for these `TokensLocked` events.
3.  **Relay & Verify**: Upon detecting an event, the listener verifies its legitimacy and relays the information to the destination chain.
4.  **Mint**: A corresponding `Bridge` contract on the destination chain receives this information, verifies it, and mints an equivalent amount of wrapped tokens to the specified recipient's address.

This script simulates the **Listen** and **Relay** steps. It connects to a source chain node, filters for `TokensLocked` events, processes them, and sends the data to a mock relayer API endpoint.

## Code Architecture

The script is designed with a clear separation of concerns, organized into several distinct classes:

-   **`Config`**: A static class responsible for loading and validating configuration from environment variables (`.env` file). This centralizes all configuration management.

-   **`BlockchainConnector`**: An abstraction layer for `web3.py`. It manages the connection to an Ethereum-compatible node, handles connection checks, and provides a clean interface for fetching contract instances and blockchain data. This makes the core logic independent of the specific web3 library details.

-   **`StateManager`**: Manages the state of processed events to prevent double-spending or replay attacks. In this simulation, it uses a simple in-memory `set`. In a production environment, this would be replaced with a connection to a persistent database like Redis or PostgreSQL for fault tolerance.

-   **`TransactionRelayer`**: Simulates communication with a relayer service. It takes processed event data, formats it into a JSON payload, and POSTs it to a configured API endpoint using the `requests` library. It includes basic retry logic with exponential backoff to handle transient network issues.

    ```json
    // Example JSON payload sent to the relayer
    {
      "transactionId": "12345",
      "sender": "0xSenderAddress...",
      "recipient": "0xRecipientAddress...",
      "amount": 1000000000000000000,
      "sourceChainId": 1,
      "destinationChainId": 42161
    }
    ```

-   **`CrossChainEventListener`**: The main orchestrator. It ties all the other components together. Its `run()` method contains the main loop that periodically polls the blockchain for new events, processes them through the `_process_event` method, and uses the `TransactionRelayer` and `StateManager` to handle the subsequent steps.

This modular architecture makes the system easier to test, maintain, and extend. A simplified view of how these components are initialized and run:

```python
# A simplified view from the main script

def main():
    config = Config()
    connector = BlockchainConnector(config.SOURCE_CHAIN_RPC_URL)
    state_manager = StateManager()
    relayer = TransactionRelayer(config.RELAYER_API_ENDPOINT)

    listener = CrossChainEventListener(
        config=config,
        connector=connector,
        state_manager=state_manager,
        relayer=relayer
    )

    listener.run()

if __name__ == "__main__":
    main()
```

## How it Works

The operational flow of the script is as follows:

1.  **Initialization**: The script starts, and the `Config` class loads required parameters (RPC URL, contract address, etc.) from a `.env` file.
2.  **Connection**: The `CrossChainEventListener` instantiates a `BlockchainConnector`, which establishes a connection to the specified source chain's RPC endpoint.
3.  **Contract Setup**: The listener initializes a `web3.py` contract object representing the on-chain `Bridge` contract using its address and a predefined ABI.
4.  **Polling Loop**: The script enters an infinite `while` loop.
5.  **Block Range Query**: In each iteration, it determines a range of blocks to scan (from the last scanned block to the current latest block).
6.  **Event Filtering**: It creates a filter on the `Bridge` contract for the `TokensLocked` event within that block range.
7.  **Event Processing**: If any events are found:
    a.  The script iterates through each event.
    b.  It extracts a unique `transactionId` from the event data.
    c.  It checks the `StateManager` to see if this `transactionId` has already been processed. If so, the event is skipped to prevent duplicates.
    d.  If the event is new, its data is packaged and passed to the `TransactionRelayer`.
8.  **Relaying**: The `TransactionRelayer` sends the event data via an HTTP POST request to the configured API endpoint.
9.  **State Update**: If the relay was successful, the `StateManager` is updated to mark the `transactionId` as processed.
10. **Wait**: The script pauses for a configured interval (`POLLING_INTERVAL_SECONDS`) before starting the next iteration of the loop.

This process continues indefinitely, ensuring near-real-time processing of cross-chain transactions.

## Getting Started

Follow these steps to run the event listener simulation.

**1. Clone the repository:**
```bash
git clone https://github.com/your-username/bugs-hunt.git
cd bugs-hunt
```

**2. Create a virtual environment and install dependencies:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
pip install -r requirements.txt
```

**3. Create a configuration file:**

Create a file named `.env` in the root directory and add the following content. You will need an RPC URL from a node provider like Infura or Alchemy for a public testnet (e.g., Sepolia).

```dotenv
# .env file

# RPC URL for the source chain (e.g., Ethereum Sepolia Testnet)
SOURCE_CHAIN_RPC_URL="https://sepolia.infura.io/v3/YOUR_INFURA_PROJECT_ID"

# Address of the Bridge smart contract to monitor.
# NOTE: The default script looks for a 'TokensLocked' event. The example address below
# is for the Chainlink Token on Sepolia, which emits 'Transfer' events. To see output
# with this address, you would need to modify the event name in the script's code.
# For a true simulation, use the address of a bridge contract you have deployed.
BRIDGE_CONTRACT_ADDRESS="0x779877A7B0D9E8603169DdbD7836e478b4624789"

# The API endpoint of the relayer service. httpbin.org is used for testing.
RELAYER_API_ENDPOINT="https://httpbin.org/post"

# How often to poll for new blocks (in seconds)
POLLING_INTERVAL_SECONDS=15

# On first run, how many blocks back from the latest block to start scanning
START_BLOCK_OFFSET=100
```

**4. Run the script:**

```bash
python script.py
```

**5. Observe the output:**

The script will start logging its status to the console. You will see messages about connecting to the blockchain, initializing the contract, and polling for events.

When an event matching the filter is found, you will see detailed logs like this:

```log
INFO:Bugs-Hunt:Connecting to source chain at https://sepolia.infura.io/v3/...
INFO:Bugs-Hunt:Successfully connected. Latest block: 5123456
INFO:Bugs-Hunt:Starting event listener loop...
INFO:Bugs-Hunt:Scanning blocks from 5123356 to 5123456...
INFO:Bugs-Hunt:Found 1 new event(s).
INFO:Bugs-Hunt:New 'TokensLocked' event detected. Tx Hash: 0x...a1b2, TxID: 12345
INFO:TransactionRelayer:Relaying data for transaction ID: 12345
INFO:TransactionRelayer:Successfully relayed transaction ID 12345. API Response: 200
INFO:StateManager:State updated for transaction ID: 12345
```