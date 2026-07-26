from __future__ import annotations

from json import load
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "bot" / "game_data"

EXPECTED = {
    "freshwater_fish.json": 15,
    "saltwater_fish.json": 15,
    "nonmetal_minerals.json": 10,
    "metal_minerals.json": 10,
}
VALID_RARITIES = {
    "common",
    "uncommon",
    "rare",
    "epic",
    "legendary",
    "mythic",
}


def main() -> None:
    all_ids: set[str] = set()

    for filename, expected_count in EXPECTED.items():
        path = DATA_DIR / filename
        with path.open("r", encoding="utf-8") as file:
            rows = load(file)

        if len(rows) != expected_count:
            raise ValueError(
                f"{filename}: expected {expected_count} rows, got {len(rows)}"
            )

        ranks = sorted(int(row["rank"]) for row in rows)
        expected_ranks = list(range(1, expected_count + 1))
        if ranks != expected_ranks:
            raise ValueError(
                f"{filename}: ranks must be {expected_ranks}, got {ranks}"
            )

        for row in rows:
            item_id = row["id"]
            if item_id in all_ids:
                raise ValueError(f"duplicate id: {item_id}")
            all_ids.add(item_id)

            if row["rarity"] not in VALID_RARITIES:
                raise ValueError(
                    f"{filename}/{item_id}: invalid rarity {row['rarity']}"
                )
            if int(row["base_value"]) <= 0:
                raise ValueError(
                    f"{filename}/{item_id}: base_value must be positive"
                )

    print("OK: 30 fish + 20 minerals validated successfully.")


if __name__ == "__main__":
    main()
