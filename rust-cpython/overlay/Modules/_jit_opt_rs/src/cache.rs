use crate::Inconsistent;

const DEPTHS: usize = 4;

/// Generated output depths (-1 means unavailable), safe greedy input depths,
/// and a 0/1 marker for instructions omitted from the emitted trace.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct CacheUop {
    pub output: [i8; DEPTHS],
    pub preferred: [u8; DEPTHS],
    pub skip: u8,
}

#[derive(Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Debug)]
struct Cost {
    transitions: u32,
    moves: u32,
}

impl Cost {
    fn transition(self, from: usize, to: usize) -> Self {
        Self {
            transitions: self.transitions + u32::from(from != to),
            moves: self.moves + from.abs_diff(to) as u32,
        }
    }
}

#[derive(Clone, Copy, Default)]
struct Parent {
    previous: u8,
    input: u8,
}

/// Chooses input cache depths across the whole trace. The initial depth is zero
/// and the final depth matches the generated greedy plan. Minimize explicit
/// spill/reload stencils, then transferred stack references. Reloads never exceed
/// the generated safe greedy input depth: speculative stack prefetch is forbidden.
/// Equal-cost plans and allocation failure preserve the existing allocator.
pub fn plan_cache(uops: &[CacheUop]) -> Result<Option<Vec<u8>>, Inconsistent> {
    if uops.len() > (u32::MAX / 3) as usize {
        return Err(Inconsistent);
    }
    let mut greedy = Cost::default();
    let mut final_depth = 0;
    for uop in uops {
        if uop.skip > 1 {
            return Err(Inconsistent);
        }
        if uop.skip == 1 {
            continue;
        }
        if uop.output.iter().any(|&depth| !(-1..DEPTHS as i8).contains(&depth))
            || uop.preferred.iter().any(|&input| {
                usize::from(input) >= DEPTHS || uop.output[usize::from(input)] < 0
            })
        {
            return Err(Inconsistent);
        }
        let input = usize::from(uop.preferred[final_depth]);
        greedy = greedy.transition(final_depth, input);
        final_depth = uop.output[input] as usize;
    }
    let mut parents = Vec::new();
    let mut plan = Vec::new();
    if parents.try_reserve_exact(uops.len()).is_err()
        || plan.try_reserve_exact(uops.len()).is_err()
    {
        return Ok(None);
    }
    let mut costs = [None; DEPTHS];
    costs[0] = Some(Cost::default());
    for uop in uops {
        let mut next = [None; DEPTHS];
        let mut row = [Parent::default(); DEPTHS];
        for (previous, &cost) in costs.iter().enumerate() {
            let Some(cost) = cost else { continue };
            if uop.skip == 1 {
                next[previous] = Some(cost);
                row[previous] = Parent { previous: previous as u8, input: previous as u8 };
                continue;
            }
            for input in (0..=usize::from(uop.preferred[previous])).rev() {
                let output = uop.output[input];
                if output < 0 {
                    continue;
                }
                let output = output as usize;
                let candidate = cost.transition(previous, input);
                if next[output].is_none_or(|current| candidate < current) {
                    next[output] = Some(candidate);
                    row[output] = Parent { previous: previous as u8, input: input as u8 };
                }
            }
        }
        parents.push(row);
        costs = next;
    }
    let Some(optimum) = costs[final_depth] else { return Err(Inconsistent) };
    if optimum >= greedy {
        return Ok(None);
    }
    plan.resize(uops.len(), 0);
    for (index, row) in parents.iter().enumerate().rev() {
        let parent = row[final_depth];
        plan[index] = parent.input;
        final_depth = usize::from(parent.previous);
    }
    Ok(Some(plan))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn op(output: [i8; DEPTHS]) -> CacheUop {
        let mut preferred = [0; DEPTHS];
        for (previous, input) in preferred.iter_mut().enumerate() {
            *input = (0..DEPTHS).filter(|&depth| output[depth] >= 0)
                .min_by_key(|&depth| previous.abs_diff(depth)).unwrap() as u8;
        }
        CacheUop { output, preferred, skip: 0 }
    }

    fn cost(uops: &[CacheUop], plan: &[u8]) -> (Cost, usize) {
        let mut depth = 0;
        let mut cost = Cost::default();
        for (uop, &input) in uops.iter().zip(plan) {
            if uop.skip == 1 { continue }
            let input = usize::from(input);
            assert!(input <= usize::from(uop.preferred[depth]));
            assert!(uop.output[input] >= 0);
            cost = cost.transition(depth, input);
            depth = uop.output[input] as usize;
        }
        (cost, depth)
    }

    fn greedy(uops: &[CacheUop]) -> Vec<u8> {
        let mut depth = 0;
        uops.iter().map(|uop| {
            if uop.skip == 1 { return depth as u8 }
            let input = uop.preferred[depth];
            depth = uop.output[usize::from(input)] as usize;
            input
        }).collect()
    }

    fn exhaustive(uops: &[CacheUop], index: usize, depth: usize, total: Cost,
                  final_depth: usize, optimum: &mut Cost) {
        if index == uops.len() {
            if depth == final_depth { *optimum = (*optimum).min(total) }
            return;
        }
        let uop = uops[index];
        if uop.skip == 1 {
            return exhaustive(uops, index + 1, depth, total, final_depth, optimum);
        }
        for input in 0..=usize::from(uop.preferred[depth]) {
            if uop.output[input] >= 0 {
                exhaustive(uops, index + 1, uop.output[input] as usize,
                    total.transition(depth, input), final_depth, optimum);
            }
        }
    }

    #[test]
    fn agrees_with_exhaustive_search_and_never_prefetches() {
        let operations = [op([1, 2, 3, -1]), op([0, 0, 1, 2]),
                          op([1, 1, 1, 2]), op([0, -1, -1, -1]),
                          CacheUop { output: [0; 4], preferred: [0; 4], skip: 1 }];
        let mut improved = 0;
        for mut number in 0..operations.len().pow(6) {
            let mut trace = Vec::new();
            for _ in 0..6 {
                trace.push(operations[number % operations.len()]);
                number /= operations.len();
            }
            let original = greedy(&trace);
            let (original_cost, final_depth) = cost(&trace, &original);
            let mut optimum = original_cost;
            exhaustive(&trace, 0, 0, Cost::default(), final_depth, &mut optimum);
            let planned = plan_cache(&trace).unwrap();
            improved += usize::from(planned.is_some());
            let result = planned.unwrap_or(original);
            assert_eq!(cost(&trace, &result), (optimum, final_depth));
        }
        assert!(improved > 0);
    }

    #[test]
    fn invalid_maps_and_short_ffi_output_are_transactional() {
        let invalid = CacheUop { output: [-1; 4], preferred: [0; 4], skip: 0 };
        assert!(matches!(plan_cache(&[invalid]), Err(Inconsistent)));
        let invalid = CacheUop { output: [0; 4], preferred: [4; 4], skip: 0 };
        assert!(matches!(plan_cache(&[invalid]), Err(Inconsistent)));
        let mut output = [0xee; 2];
        // SAFETY: disjoint live descriptors and writable output with the declared capacity.
        let status = unsafe { crate::_PyJitOpt_PlanCache(&invalid, 1, output.as_mut_ptr(), 2) };
        assert_eq!(status, -1);
        assert_eq!(output, [0xee; 2]);
        let valid = [op([1, 2, 3, -1]); 5];
        assert!(plan_cache(&valid).unwrap().is_some());
        // SAFETY: the two-byte output is valid but deliberately too short.
        let status = unsafe { crate::_PyJitOpt_PlanCache(valid.as_ptr(), valid.len(), output.as_mut_ptr(), output.len()) };
        assert_eq!(status, 0);
        assert_eq!(output, [0xee; 2]);
        assert_eq!(std::mem::size_of::<CacheUop>(), 9);
    }
}
