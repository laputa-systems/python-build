use crate::Inconsistent;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct AttributeUop { pub opcode: u16, pub target: u32 }
#[repr(C)]
#[derive(Clone, Copy)]
pub struct AttributeOpcodes {
    pub check: u16, pub load: u16, pub checked: u16, pub nop: u16,
    pub virtual_begin: u16, pub virtual_return: u16,
}
const _: () = {
    assert!(std::mem::size_of::<AttributeUop>() == 8);
    assert!(std::mem::offset_of!(AttributeUop, target) == 4);
    assert!(std::mem::size_of::<AttributeOpcodes>() == 12);
    assert!(std::mem::offset_of!(AttributeOpcodes, virtual_return) == 10);
};

pub fn plan(uops: &[AttributeUop], ops: &AttributeOpcodes) -> Result<Option<Vec<u16>>, Inconsistent> {
    let ids = [ops.check, ops.load, ops.checked, ops.nop, ops.virtual_begin, ops.virtual_return];
    if ids.iter().any(|id| *id == 0) || ids.iter().enumerate().any(|(i, id)| ids[..i].contains(id)) || uops.len() > u32::MAX as usize {
        return Err(Inconsistent);
    }
    if uops.iter().any(|u| u.opcode == 0) { return Err(Inconsistent); }
    if uops.iter().any(|u| u.opcode == ops.virtual_begin || u.opcode == ops.virtual_return) {
        return Ok(None);
    }
    let mut steps = Vec::new();
    if steps.try_reserve_exact(uops.len()).is_err() { return Ok(None); }
    steps.extend(uops.iter().map(|u| u.opcode));
    let mut changed = false;
    for i in 0..uops.len().saturating_sub(1) {
        if uops[i].opcode == ops.check && uops[i + 1].opcode == ops.load && uops[i].target == uops[i + 1].target {
            steps[i] = ops.nop;
            steps[i + 1] = ops.checked;
            changed = true;
        }
    }
    Ok(changed.then_some(steps))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::_PyJitOpt_PlanAttributeLoads;
    #[test]
    fn ffi_initializes_only_complete_output() {
        let ops = AttributeOpcodes { check: 1, load: 2, checked: 3, nop: 4, virtual_begin: 5, virtual_return: 6 };
        let input = [AttributeUop { opcode: 1, target: 9 }, AttributeUop { opcode: 2, target: 9 }];
        let sentinel = 99_u16;
        let mut out = [sentinel; 2];
        // SAFETY: all calls use live, aligned, disjoint input and output arrays.
        assert_eq!(unsafe { _PyJitOpt_PlanAttributeLoads(input.as_ptr(), 2, &ops, out.as_mut_ptr(), 1) }, 0);
        assert_eq!(out, [sentinel; 2]);
        let bad = AttributeOpcodes { load: 1, ..ops };
        // SAFETY: all calls use live, aligned, disjoint input and output arrays.
        assert_eq!(unsafe { _PyJitOpt_PlanAttributeLoads(input.as_ptr(), 2, &bad, out.as_mut_ptr(), 2) }, -1);
        assert_eq!(out, [sentinel; 2]);
        // SAFETY: all calls use live, aligned, disjoint input and output arrays.
        assert_eq!(unsafe { _PyJitOpt_PlanAttributeLoads(input.as_ptr(), 2, &ops, out.as_mut_ptr(), 2) }, 2);
        assert_eq!(out, [4, 3]);
        let invalid = [AttributeUop { opcode: 0, target: 9 }];
        // SAFETY: all calls use live, aligned, disjoint input and output arrays.
        assert_eq!(unsafe { _PyJitOpt_PlanAttributeLoads(invalid.as_ptr(), 1, &ops, out.as_mut_ptr(), 2) }, -1);
        let differing = [input[0], AttributeUop { opcode: 2, target: 10 }];
        assert!(plan(&differing, &ops).unwrap().is_none());
        let separated = [input[0], AttributeUop { opcode: 4, target: 9 }, input[1]];
        assert!(plan(&separated, &ops).unwrap().is_none());
        let virtual_trace = [input[0], input[1], AttributeUop { opcode: 5, target: 9 }];
        assert!(plan(&virtual_trace, &ops).unwrap().is_none());
    }
}
