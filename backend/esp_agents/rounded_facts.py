"""Model-ready values: deterministic half-up rounding, no arithmetic by an LLM."""
from decimal import Decimal, ROUND_HALF_UP
from .numeric_guard import narrative_scope, _dimension_for_field


def rounded(value: float, places: int) -> str:
    return str(Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def rounded_narrative_scope(result, candidate_rank=1):
    def walk(value, field=""):
        if isinstance(value,bool): return value
        if isinstance(value,float):
            dim=_dimension_for_field(field)
            if dim=="frac": return rounded(value*100,1)+"%"
            if dim=="ft": return rounded(value,0)+" ft"
            if dim in {"hz","hp","psi","f","bpd","months","in"}:
                return rounded(value,1)+" "+dim
            return rounded(value,3)
        if isinstance(value,dict): return {k:walk(v,k) for k,v in value.items()}
        if isinstance(value,list): return [walk(v,field) for v in value]
        return value
    return walk(narrative_scope(result,candidate_rank=candidate_rank))
