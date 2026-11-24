"""Step-guarded copy of vLLM 0.19.1 `_parse_gemma4_array` (LOCAL EVAL ONLY, never in a submission).

Identical to the pinned function except for one added no-progress guard in the bare-value
branch: where the pinned code would loop forever on a stray ']', this copy calls `on_fatal`
(so the capture shim can log SCORER_FATAL with the input) and skips the character, which is
the behaviour of upstream's fixed parser. `self_test()` checks equivalence on non-hanging input.
"""
from __future__ import annotations

STRING_DELIM = '<|"|>'


def make_guarded(parse_args, parse_value, on_fatal):
    """Build a guarded _parse_gemma4_array bound to the given module's helpers."""

    def _parse_gemma4_array(arr_str: str, *, partial: bool = False) -> list:
        items: list = []
        i = 0
        n = len(arr_str)
        while i < n:
            while i < n and arr_str[i] in (" ", ",", "\n", "\t"):
                i += 1
            if i >= n:
                break
            if arr_str[i:].startswith(STRING_DELIM):
                i += len(STRING_DELIM)
                end_pos = arr_str.find(STRING_DELIM, i)
                if end_pos == -1:
                    items.append(arr_str[i:])
                    break
                items.append(arr_str[i:end_pos])
                i = end_pos + len(STRING_DELIM)
            elif arr_str[i] == "{":
                depth = 1
                obj_start = i + 1
                i += 1
                while i < n and depth > 0:
                    if arr_str[i:].startswith(STRING_DELIM):
                        i += len(STRING_DELIM)
                        nd = arr_str.find(STRING_DELIM, i)
                        i = nd + len(STRING_DELIM) if nd != -1 else n
                        continue
                    if arr_str[i] == "{":
                        depth += 1
                    elif arr_str[i] == "}":
                        depth -= 1
                    i += 1
                if depth > 0:
                    items.append(parse_args(arr_str[obj_start:i], partial=True))
                else:
                    items.append(parse_args(arr_str[obj_start : i - 1]))
            elif arr_str[i] == "[":
                depth = 1
                sub_start = i + 1
                i += 1
                while i < n and depth > 0:
                    if arr_str[i] == "[":
                        depth += 1
                    elif arr_str[i] == "]":
                        depth -= 1
                    i += 1
                if depth > 0:
                    items.append(_parse_gemma4_array(arr_str[sub_start:i], partial=True))
                else:
                    items.append(_parse_gemma4_array(arr_str[sub_start : i - 1]))
            else:
                val_start = i
                while i < n and arr_str[i] not in (",", "]"):
                    i += 1
                if i == val_start:  # ---- FIREBREAK GUARD: pinned code never advances here ----
                    on_fatal(arr_str)
                    i += 1
                    continue
                if partial and i >= n:
                    break
                items.append(parse_value(arr_str[val_start:i]))
        return items

    return _parse_gemma4_array


def self_test(module, cases: list[str]) -> dict:
    """Compare guarded vs pinned array parsing on inputs that do NOT hang the pinned code."""
    fatals: list[str] = []
    g = make_guarded(module._parse_gemma4_args, module._parse_gemma4_value, fatals.append)
    same = 0
    for c in cases:
        if g(c) == module._parse_gemma4_array(c):
            same += 1
    return {"cases": len(cases), "identical": same, "fatals": len(fatals)}
