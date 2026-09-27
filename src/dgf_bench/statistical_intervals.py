"""Deterministic case-cluster bootstrap, stratified by route."""
import math
import random
from collections import defaultdict

def case_wilson(p, n):
    """Descriptive fallback using independent cases, not correlated gates."""
    z=1.959963984540054; denom=1+z*z/n
    center=(p+z*z/(2*n))/denom
    margin=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return max(0,center-margin), min(1,center+margin)

def cluster_interval(units, draws=2000):
    # Each unit is (route, successes, trials) for one independent dossier.
    if not units: return None, None
    n=len(units); total=sum(x[2] for x in units); success=sum(x[1] for x in units)
    if not total: return None, None
    if success in (0,total) or n==1:
        # Ordinary bootstrap degenerates at the boundary. Use independent case
        # count, never gate count, for a conservative descriptive Wilson bound.
        return case_wilson(success/total,n)
    strata=defaultdict(list)
    for unit in units: strata[unit[0]].append(unit)
    rng=random.Random(81931); ratios=[]
    for _ in range(draws):
        sample=[rng.choice(rows) for rows in strata.values() for _ in rows]
        ratios.append(sum(x[1] for x in sample)/sum(x[2] for x in sample))
    ratios.sort()
    low,high=ratios[int(.025*draws)], ratios[min(draws-1,int(.975*draws))]
    if low == high:
        # Homogeneous strata can also collapse the bootstrap away from 0/1.
        # Do not advertise a zero-width confidence interval from seven cases.
        return case_wilson(success/total,n)
    return low,high
