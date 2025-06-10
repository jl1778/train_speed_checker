import requests
import time
import json
from collections import defaultdict
import os

API_KEY = os.getenv("WMATA_API_KEY")
POLL_INTERVAL = 60  # seconds
NUM_POLLS = 1    # Adjust as needed
OUTPUT_FILE = "circuit_rolling_averages.json"

def fetch_train_positions():
    url = "https://api.wmata.com/TrainPositions/TrainPositions?contentType=json"
    headers = {"api_key": API_KEY}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()["TrainPositions"]

def build_circuit_map():
    path = os.path.join(os.path.dirname(__file__), "circuit_map.json")
    with open(path) as f:
        circuit_map_outer = json.load(f)
    # The value of "wmata_standard_routes" is a stringified JSON, so parse it
    wmata_routes = json.loads(circuit_map_outer["wmata_standard_routes"])
    # Return just the "data" dict, which maps circuit keys to their info
    return wmata_routes["data"]

def load_rolling_averages():
    if not os.path.exists(OUTPUT_FILE):
        return {}
    with open(OUTPUT_FILE, "r") as f:
        return json.load(f)

def save_rolling_averages(output):
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

def main():
    circuit_map = build_circuit_map()
    last_positions = {}
    # { (LineCode, Direction): {seqnum: {"sum": float, "count": int}} }
    rolling_averages = defaultdict(lambda: defaultdict(lambda: {"sum": 0.0, "count": 0}))
    # Load previous averages if present
    output = load_rolling_averages()
    # Track how many polls have contributed to each average
    poll_counts = defaultdict(lambda: defaultdict(int))

    # If previous output exists, load into rolling_averages and poll_counts
    for line_code, directions in output.items():
        if line_code == "_counts":
            continue
        for direction, arr in directions.items():
            for idx, avg in enumerate(arr):
                if avg is not None:
                    # Store as sum/count, count is stored in a parallel structure
                    count = output.get("_counts", {}).get(line_code, {}).get(direction, [0]*len(arr))[idx]
                    if count > 0:
                        rolling_averages[(line_code, direction)][idx]["sum"] = avg * count
                        rolling_averages[(line_code, direction)][idx]["count"] = count
                        poll_counts[line_code][direction] = max(poll_counts[line_code][direction], count)

    direction_map = {"1": "east", "2": "west"}

    for poll_num in range(NUM_POLLS):
        print(f"Polling {poll_num+1}/{NUM_POLLS}...")
        now = time.time()
        trains = fetch_train_positions()
        for train in trains:
            line_code = train.get("LineCode")
            direction_num = train.get("DirectionNum")
            if not line_code or not direction_num:
                continue
            direction_num = str(direction_num)
            circuit_id = train.get("CircuitId")
            if not circuit_id:
                continue
            circuit_key = f"{circuit_id}{line_code}"
            circuit = circuit_map.get(circuit_key)
            if not circuit:
                continue
            direction = circuit.get("Direction")
            seq_num = circuit["SeqNum"]

            # Only store if direction_num matches circuit direction
            if direction_map.get(direction_num) != direction:
                print(f"Skipping train {train['TrainId']} due to direction mismatch")
                continue

            train_key = (train["TrainId"], line_code, direction)
            last = last_positions.get(train_key)
            if last:
                last_seq, last_time = last
                if seq_num != last_seq:
                    # Distribute time across all segments traversed
                    num_segments = abs(seq_num - last_seq)
                    print(num_segments)
                    if num_segments > 0:
                        time_diff = now - last_time
                        avg_time_per_segment = time_diff / num_segments
                        print(
                            f"Train {train['TrainId']} | {line_code} {direction} | "
                            f"seq {last_seq}->{seq_num} | "
                            f"time_diff={time_diff:.2f}s | "
                            f"num_segments={num_segments} | "
                            f"avg_time_per_segment={avg_time_per_segment:.2f}s"
                        )
                        for idx in range(min(seq_num, last_seq), max(seq_num, last_seq)):
                            key = (line_code, direction)
                            rolling_averages[key][idx]["sum"] += avg_time_per_segment
                            rolling_averages[key][idx]["count"] += 1
                    elif num_segments == 0:
                        time_diff = now - last_time
                        # accounting for time spent at the same seqnum
                        print(
                            f"Train {train['TrainId']} | {line_code} {direction} | "
                            f"seq {seq_num} (no change) | "
                            f"time_diff={time_diff:.2f}s"
                        )
                        if direction == "east":
                            rolling_averages[key][seq_num+1]["sum"] += time_diff
                            rolling_averages[key][seq_num+1]["count"] += 1
                        elif seq_num > 0:
                            rolling_averages[key][seq_num-1]["sum"] += time_diff
                            rolling_averages[key][seq_num-1]["count"] += 1



            last_positions[train_key] = (seq_num, now)

        # Build output: for each line and direction, an array where index is seqnum and value is avg time to next seqnum
        output = {}
        counts_output = {}
        for (line_code, direction), seq_dict in rolling_averages.items():
            if line_code not in output:
                output[line_code] = {}
                counts_output[line_code] = {}
            max_seq = max(seq_dict.keys()) if seq_dict else 0
            arr = []
            counts_arr = []
            for i in range(max_seq + 1):
                entry = seq_dict.get(i)
                if entry and entry["count"] > 0:
                    arr.append(entry["sum"] / entry["count"])
                    counts_arr.append(entry["count"])
                else:
                    arr.append(None)
                    counts_arr.append(0)
            output[line_code][direction] = arr
            counts_output[line_code][direction] = counts_arr
        # Store counts under a special key
        output["_counts"] = counts_output
        save_rolling_averages(output)
        print(f"Rolling averages updated after poll {poll_num+1}")

        time.sleep(POLL_INTERVAL)

    print(f"Rolling averages saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()