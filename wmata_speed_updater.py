import requests
import time
import json
from collections import defaultdict
import os

API_KEY = os.getenv("WMATA_API_KEY")
OUTPUT_FILE = "circuit_rolling_averages.json"
LAST_POSITIONS_FILE = "last_positions.json"

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
    wmata_routes = json.loads(circuit_map_outer["wmata_standard_routes"])
    return wmata_routes["data"]

def load_json(filename, default):
    if os.path.exists(filename):
        with open(filename) as f:
            return json.load(f)
    return default

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def main():
    circuit_map = build_circuit_map()
    last_positions = load_json(LAST_POSITIONS_FILE, {})
    output = load_json(OUTPUT_FILE, {})

    rolling_averages = defaultdict(lambda: defaultdict(lambda: {"sum": 0.0, "count": 0}))
    poll_counts = defaultdict(lambda: defaultdict(int))

    for line_code, directions in output.items():
        if line_code == "_counts":
            continue
        for direction, arr in directions.items():
            for idx, avg in enumerate(arr):
                if avg is not None:
                    count = output.get("_counts", {}).get(line_code, {}).get(direction, [0]*len(arr))[idx]
                    if count > 0:
                        rolling_averages[(line_code, direction)][idx]["sum"] = avg * count
                        rolling_averages[(line_code, direction)][idx]["count"] = count
                        poll_counts[line_code][direction] = max(poll_counts[line_code][direction], count)

    now = time.time()
    trains = fetch_train_positions()
    direction_map = {"1": "east", "2": "west"}

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

        if direction_map.get(direction_num) != direction:
            continue

        train_key = (train["TrainId"], line_code, direction)
        last = last_positions.get("|".join(train_key))  # store as flat string for JSON

        if last:
            last_seq, last_time = last
            if seq_num != last_seq:
                num_segments = abs(seq_num - last_seq)
                if num_segments > 0:
                    time_diff = now - last_time
                    avg_time_per_segment = time_diff / num_segments
                    for idx in range(min(seq_num, last_seq), max(seq_num, last_seq)):
                        rolling_averages[(line_code, direction)][idx]["sum"] += avg_time_per_segment
                        rolling_averages[(line_code, direction)][idx]["count"] += 1
            else:
                time_diff = now - last_time
                if direction == "east":
                    rolling_averages[(line_code, direction)][seq_num + 1]["sum"] += time_diff
                    rolling_averages[(line_code, direction)][seq_num + 1]["count"] += 1
                elif seq_num > 0:
                    rolling_averages[(line_code, direction)][seq_num - 1]["sum"] += time_diff
                    rolling_averages[(line_code, direction)][seq_num - 1]["count"] += 1

        last_positions["|".join(train_key)] = [seq_num, now]

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

    output["_counts"] = counts_output
    save_json(OUTPUT_FILE, output)
    save_json(LAST_POSITIONS_FILE, last_positions)
    print("Rolling averages updated")

if __name__ == "__main__":
    main()
