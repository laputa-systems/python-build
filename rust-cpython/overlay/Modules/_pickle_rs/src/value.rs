use std::borrow::Cow;

use deser::de::{Deserialize, Sink, SinkHandle, default_atom};
use deser::ser::{Emit, MapEmitter, SeqEmitter, Serialize, SerializeHandle};
use deser::{Atom, Error, ErrorKind, State};
use deser_pickle::SerializerConfig;

const EXPECTING: &str = "a supported builtin pickle value";
const OPCODE_PROTOCOL: u8 = 3;

pub enum PickleValue {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Bytes(Vec<u8>),
    String(String),
    List(Vec<PickleValue>),
    Dict(Vec<(PickleValue, PickleValue)>),
}

impl PickleValue {
    pub fn is_dict_key(&self) -> bool {
        matches!(
            self,
            Self::None | Self::Bool(_) | Self::Int(_) | Self::Bytes(_) | Self::String(_)
        )
    }
}

pub fn encode(value: &PickleValue) -> Result<Vec<u8>, Error> {
    const CONFIG: SerializerConfig = SerializerConfig::builder().protocol(OPCODE_PROTOCOL).build();
    CONFIG.to_vec(value)
}

pub fn decode(input: &[u8]) -> Result<PickleValue, Error> {
    deser_pickle::from_slice(input)
}

impl Serialize for PickleValue {
    fn serialize<'a>(value: &'a Self, state: &mut State) -> Result<Emit<'a>, Error> {
        Ok(match value {
            Self::None => Emit::Atom(Atom::Null),
            Self::Bool(value) => Emit::from(*value),
            Self::Int(value) => Emit::from(*value),
            Self::Float(value) => Emit::from(*value),
            Self::Bytes(value) => Emit::from(value.as_slice()),
            Self::String(value) => Emit::from(value.as_str()),
            Self::List(values) => Emit::seq(
                ListEmitter {
                    values: values.iter(),
                },
                state,
            ),
            Self::Dict(entries) => Emit::map(
                DictEmitter {
                    entries: entries.iter(),
                    value: None,
                },
                state,
            ),
        })
    }
}

struct ListEmitter<'a> {
    values: std::slice::Iter<'a, PickleValue>,
}

impl SeqEmitter for ListEmitter<'_> {
    fn next(&mut self, _state: &mut State) -> Result<Option<SerializeHandle<'_>>, Error> {
        Ok(self.values.next().map(SerializeHandle::to))
    }
}

struct DictEmitter<'a> {
    entries: std::slice::Iter<'a, (PickleValue, PickleValue)>,
    value: Option<&'a PickleValue>,
}

impl MapEmitter for DictEmitter<'_> {
    fn next_key(&mut self, _state: &mut State) -> Result<Option<SerializeHandle<'_>>, Error> {
        Ok(self.entries.next().map(|(key, value)| {
            self.value = Some(value);
            SerializeHandle::to(key)
        }))
    }

    fn next_value(&mut self, _state: &mut State) -> Result<SerializeHandle<'_>, Error> {
        let value = self
            .value
            .take()
            .ok_or_else(|| Error::new(ErrorKind::InvalidState, "missing map key"))?;
        Ok(SerializeHandle::to(value))
    }
}

fn atom_value(atom: Atom) -> Result<PickleValue, Error> {
    Ok(match atom {
        Atom::Null => PickleValue::None,
        Atom::Bool(value) => PickleValue::Bool(value),
        Atom::I64(value) => PickleValue::Int(value),
        Atom::U64(value) => PickleValue::Int(i64::try_from(value).map_err(|_| {
            Error::new(
                ErrorKind::InvalidValue,
                "integer does not fit a 64-bit signed integer",
            )
        })?),
        Atom::F64(value) => PickleValue::Float(value),
        Atom::Str(value) => PickleValue::String(value.into_owned()),
        Atom::Bytes(value) => PickleValue::Bytes(value.into_owned()),
        other => return Err(other.unexpected_error(EXPECTING)),
    })
}

impl<'de> Deserialize<'de> for PickleValue {
    fn deserialize_into<'out>(
        out: &'out mut Option<Self>,
        state: &mut State,
    ) -> SinkHandle<'out, 'de> {
        SinkHandle::arena(
            ValueSink {
                out,
                building: Building::Scalar,
                key: None,
                slot: None,
            },
            state,
        )
    }

    fn expecting() -> Cow<'static, str> {
        Cow::Borrowed(EXPECTING)
    }
}

enum Building {
    Scalar,
    List(Vec<PickleValue>),
    Dict(Vec<(PickleValue, PickleValue)>),
}

struct ValueSink<'a> {
    out: &'a mut Option<PickleValue>,
    building: Building,
    key: Option<PickleValue>,
    slot: Option<PickleValue>,
}

impl ValueSink<'_> {
    fn flush(&mut self) {
        let Some(value) = self.slot.take() else {
            return;
        };
        match &mut self.building {
            Building::List(values) => values.push(value),
            Building::Dict(entries) => {
                if let Some(key) = self.key.take() {
                    entries.push((key, value));
                }
            }
            Building::Scalar => {}
        }
    }
}

impl<'de> Sink<'de> for ValueSink<'_> {
    fn atom(&mut self, atom: Atom, state: &mut State) -> Result<(), Error> {
        match self.building {
            Building::Scalar => {
                *self.out = Some(atom_value(atom)?);
                Ok(())
            }
            _ => default_atom(self, atom, state),
        }
    }

    fn map(&mut self, _state: &mut State) -> Result<(), Error> {
        self.building = Building::Dict(Vec::new());
        Ok(())
    }

    fn seq(&mut self, _state: &mut State) -> Result<(), Error> {
        self.building = Building::List(Vec::new());
        Ok(())
    }

    fn next_key(&mut self, state: &mut State) -> Result<SinkHandle<'_, 'de>, Error> {
        self.flush();
        Ok(PickleValue::deserialize_into(&mut self.slot, state))
    }

    fn next_value(&mut self, state: &mut State) -> Result<SinkHandle<'_, 'de>, Error> {
        if matches!(self.building, Building::Dict(_)) {
            let key = self
                .slot
                .take()
                .ok_or_else(|| Error::new(ErrorKind::InvalidState, "missing map key"))?;
            if !key.is_dict_key() {
                return Err(Error::new(ErrorKind::InvalidType, "unsupported dict key"));
            }
            self.key = Some(key);
        } else {
            self.flush();
        }
        Ok(PickleValue::deserialize_into(&mut self.slot, state))
    }

    fn finish(&mut self, _state: &mut State) -> Result<(), Error> {
        self.flush();
        match std::mem::replace(&mut self.building, Building::Scalar) {
            Building::Scalar => {}
            Building::List(values) => *self.out = Some(PickleValue::List(values)),
            Building::Dict(entries) => *self.out = Some(PickleValue::Dict(entries)),
        }
        Ok(())
    }

    fn expecting(&self) -> Cow<'_, str> {
        Cow::Borrowed(EXPECTING)
    }
}
