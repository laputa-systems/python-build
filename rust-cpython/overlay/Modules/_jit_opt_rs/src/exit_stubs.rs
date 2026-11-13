use crate::Inconsistent;

/// C descriptors use kinds 0 (ordinary), 1 (virtual entry), 2 (virtual return),
/// and 3 (resume exit). Every field is initialized; exit fields are read only
/// for kind 3. Entry refusals belong to the surrounding physical frame.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct ExitStubUop {
    pub kind: u8,
    pub exit_variant: u16,
    pub target: u32,
}

enum Kind { Other, Begin, Return, Resume }
impl TryFrom<u8> for Kind {
    type Error = Inconsistent;
    fn try_from(value: u8) -> Result<Self, Inconsistent> {
        match value { 0 => Ok(Self::Other), 1 => Ok(Self::Begin), 2 => Ok(Self::Return), 3 => Ok(Self::Resume), _ => Err(Inconsistent) }
    }
}

enum Region {
    Physical,
    Virtual { previous: Option<((u32, u16), u32)> },
}

/// Reuses the previous matching resume recipe within one virtual region.
/// Canonical indices preserve target and exit-cache variant. Region boundaries
/// exclude entry refusals and prevent reuse across calls. Invalid descriptors
/// reject the plan; allocation failure preserves the original emission.
pub fn plan(uops: &[ExitStubUop]) -> Result<Option<Vec<u32>>, Inconsistent> {
    if uops.len() > u32::MAX as usize { return Err(Inconsistent); }
    let mut output = Vec::new();
    if output.try_reserve_exact(uops.len()).is_err() { return Ok(None); }
    let mut region = Region::Physical;
    for (i, uop) in uops.iter().enumerate() {
        let index = u32::try_from(i).map_err(|_| Inconsistent)?;
        let mut source = index;
        match Kind::try_from(uop.kind)? {
            Kind::Other => (),
            Kind::Begin => match region {
                Region::Physical => region = Region::Virtual { previous: None },
                Region::Virtual { .. } => return Err(Inconsistent),
            },
            Kind::Return => match region {
                Region::Virtual { .. } => region = Region::Physical,
                Region::Physical => return Err(Inconsistent),
            },
            Kind::Resume => if let Region::Virtual { previous } = &mut region {
                let key = (uop.target, uop.exit_variant);
                if let Some((old, canonical)) = *previous {
                    if old == key { source = canonical; }
                }
                *previous = Some((key, source));
            },
        }
        output.push(source);
    }
    if matches!(region, Region::Virtual { .. }) { return Err(Inconsistent); }
    Ok(Some(output))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn u(kind: u8, target: u32, exit_variant: u16) -> ExitStubUop { ExitStubUop { kind, target, exit_variant } }
    #[test]
    fn ffi_layout() {
        assert_eq!(std::mem::size_of::<ExitStubUop>(), 8);
        assert_eq!(std::mem::offset_of!(ExitStubUop, kind), 0);
        assert_eq!(std::mem::offset_of!(ExitStubUop, exit_variant), 2);
        assert_eq!(std::mem::offset_of!(ExitStubUop, target), 4);
    }
    #[test]
    fn recipes_and_regions() {
        let xs = [u(3,1,1),u(1,1,1),u(3,1,1),u(0,0,0),u(3,1,1),u(3,2,1),u(3,1,1),u(3,1,2),u(2,0,0),u(1,0,0),u(3,1,2),u(2,0,0),u(3,1,2)];
        assert_eq!(plan(&xs).unwrap().unwrap(), [0,1,2,3,2,5,6,7,8,9,10,11,12]);
    }
    #[test]
    fn malformed() {
        for xs in [vec![u(9,0,0)], vec![u(2,0,0)], vec![u(1,0,0)], vec![u(1,0,0),u(1,0,0)], vec![u(1,0,0),u(2,0,0),u(2,0,0)]] { assert!(plan(&xs).is_err()); }
    }
}
