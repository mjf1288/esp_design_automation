"""One-shot catalog migration: add B.16 motor_type to every motor record.

Classification is sourced from manufacturer product-line statements, not
inferred from the model id. See SOURCES below. power_factor, efficiency and
demag_temp_f are deliberately left null: no public datasheet reachable for
this catalog publishes them per model, and inventing them would silently
change I_FL (B.14.1) and transformer kVA (B.15.1) on every design.
"""

from __future__ import annotations

import json
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[2] / "data" / "catalog" / "motors.json"

PMM_SOURCE = (
    "Manufacturer states its product line is permanent magnet motors: "
    "https://www.magneticpumpingsolutions.com/ (PMESP)"
)
NOVOMET_SOURCE = (
    "Novomet publishes this as a permanent magnet motor line: "
    "https://www.novometgroup.com/assets/files/2019/Cases/Cases%20ENG/bro-esp-permanent-magnet-motor.pdf"
)
SLB_SOURCE = (
    "SLB/REDA ESP motors are three-phase two-pole squirrel-cage induction "
    "type: https://www.slb.com/products-and-services/innovating-in-oil-and-gas/"
    "completions/artificial-lift/electrical-submersible-pumps/reda-esp-pump-system/"
    "motors/reda-maximus-eon-esp-motor"
)
BH_SOURCE = (
    "Baker Hughes lists the CENtrilift SP 450 series under ESP induction "
    "motors: https://www.bakerhughes.com/production/artificial-lift/"
    "electrical-submersible-pump-systems/centrilift-esp-induction-motors"
)

# id prefix -> (motor_type, provenance note, source url to append)
RULES: list[tuple[str, str, str, str]] = [
    ("mps-pmesp-", "permanent_magnet", PMM_SOURCE, "https://www.magneticpumpingsolutions.com/"),
    (
        "novomet-pmm-",
        "permanent_magnet",
        NOVOMET_SOURCE,
        "https://www.novometgroup.com/assets/files/2019/Cases/Cases%20ENG/bro-esp-permanent-magnet-motor.pdf",
    ),
    (
        "slb-maximus-",
        "induction",
        SLB_SOURCE,
        "https://www.slb.com/products-and-services/innovating-in-oil-and-gas/completions/artificial-lift/electrical-submersible-pumps/reda-esp-pump-system/motors/reda-maximus-eon-esp-motor",
    ),
    (
        "baker-hughes-centrilift-",
        "induction",
        BH_SOURCE,
        "https://www.bakerhughes.com/production/artificial-lift/electrical-submersible-pump-systems/centrilift-esp-induction-motors",
    ),
]

PMM_GAP_NOTE = (
    "B.16 fork: motor_type is permanent_magnet, so a VSD is mandatory and a "
    "magnet demagnetization limit applies in addition to the winding "
    "insulation class. demag_temp_f is null because no reachable public "
    "datasheet publishes it for this model; the engine therefore cannot "
    "clear the B.16 magnet gate and reports it as unverified rather than "
    "passing it."
)
PF_GAP_NOTE = (
    "power_factor and efficiency are null because the published table gives "
    "only nameplate volts/amps per HP row. B.14.1 I_FL and B.15.1 kVA stay "
    "unavailable until these are supplied."
)


def main() -> int:
    motors = json.loads(CATALOG.read_text())
    changed = 0
    for motor in motors:
        for prefix, motor_type, note, url in RULES:
            if not motor["id"].startswith(prefix):
                continue
            motor["motor_type"] = motor_type
            motor.setdefault("power_factor", None)
            motor.setdefault("efficiency", None)
            if motor_type == "permanent_magnet":
                motor.setdefault("demag_temp_f", None)
            extra = f" MOTOR TYPE 2026-08-25: {note}. {PF_GAP_NOTE}"
            if motor_type == "permanent_magnet":
                extra += f" {PMM_GAP_NOTE}"
            motor["notes"] = (motor.get("notes", "") + extra).strip()
            if url not in motor["source_urls"]:
                motor["source_urls"].append(url)
            changed += 1
            break
        else:
            raise SystemExit(f"unclassified motor {motor['id']!r}: refusing to guess a motor_type")
    CATALOG.write_text(json.dumps(motors, indent=2) + "\n")
    types = {}
    for motor in motors:
        types[motor["motor_type"]] = types.get(motor["motor_type"], 0) + 1
    print(f"classified {changed} motors: {types}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
