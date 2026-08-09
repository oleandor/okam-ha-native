'use strict';

// This script intentionally records shapes and correlation tags, never raw
// strings or memory. The host applies a second fail-closed redaction pass.
const targets = {
  'p2papi.dll': [
    'P2PAPI_Initial', 'P2PAPI_InitialWithServer', 'P2PAPI_CreateInstance',
    'P2PAPI_Connect', 'P2PAPI_SetAVDataCallBack', 'P2PAPI_SetMessageCallBack',
    'P2PAPI_StartVideo', 'P2PAPI_StopVideo', 'P2PAPI_Close', 'P2PAPI_DestroyInstance'
  ],
  'devdll_925.dll': [
    'dev_Init2', 'dev_Connect', 'dev_SetOnUnVSample', 'dev_SetOnVSample',
    'dev_SetOnStatus', 'dev_SetOnConnected', 'dev_Start', 'dev_Start2',
    'dev_Stop', 'dev_TransCGI', 'dev_put_auth', 'dev_put_IP', 'dev_put_devname'
  ],
  'ppcs_api.dll': [
    'PPCS_Initialize', 'PPCS_Connect', 'PPCS_ConnectByServer', 'PPCS_Check',
    'PPCS_Check_Buffer', 'PPCS_Read', 'PPCS_Write', 'PPCS_Close'
  ]
};

// Counts are established by the debugger stack capture and this trace. Keeping
// them explicit prevents unrelated stack words from being mistaken for ABI.
const argumentCounts = {
  'dev_Init2': 1,
  'dev_SetOnStatus': 2,
  'dev_SetOnConnected': 2,
  'dev_SetOnVSample': 2,
  'dev_SetOnUnVSample': 2,
  'dev_put_IP': 4,
  'dev_put_auth': 3,
  'dev_put_devname': 2,
  'dev_Connect': 2,
  'dev_Start': 1,
  'dev_Start2': 2,
  'dev_Stop': 1,
  'dev_TransCGI': 3
};

const hooked = new Set();
const handles = new Map();
let nextHandle = 1;
let nextCall = 1;

function fnv1a(value) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < value.length; i++) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return ('00000000' + hash.toString(16)).slice(-8);
}

function safeString(pointer) {
  if (pointer.isNull()) return {kind: 'null'};
  try {
    const bytes = [];
    for (let index = 0; index < 512; index++) {
      const value = pointer.add(index).readU8();
      if (value === 0) break;
      // Reject binary/pointer data instead of guessing its encoding.
      if (value < 0x09 || (value > 0x0d && value < 0x20)) return {kind: 'pointer'};
      bytes.push(value);
    }
    if (!bytes.length || bytes.length === 512) return {kind: 'pointer'};
    const text = String.fromCharCode.apply(null, bytes);
    return {kind: 'string', length: text.length, tag: 'fnv1a:' + fnv1a(text)};
  } catch (_) {
    return {kind: 'pointer'};
  }
}

function handle(pointer) {
  if (pointer.isNull()) return 'null';
  const numeric = pointer.toUInt32();
  if (numeric < 0x10000) return 'small:' + numeric;
  const key = pointer.toString();
  if (!handles.has(key)) handles.set(key, 'object-' + nextHandle++);
  return handles.get(key);
}

function structureShape(pointer, bytes) {
  if (pointer.isNull()) return {kind: 'null'};
  const slots = [];
  try {
    for (let offset = 0; offset < bytes; offset += 4) {
      const value = pointer.add(offset).readU32();
      if (value === 0) continue;
      slots.push({offset: offset, value: value < 0x10000 ? 'small' : 'nonzero'});
    }
    return {kind: 'structure', bytes: bytes, nonzeroSlots: slots};
  } catch (_) {
    return {kind: 'unreadable-structure', bytes: bytes};
  }
}

function describe(name, args) {
  const values = [];
  const count = argumentCounts[name] === undefined ? 6 : argumentCounts[name];
  for (let index = 0; index < count; index++) values.push({index: index, value: handle(args[index])});
  if (name === 'dev_Init2') values[0].detail = safeString(args[0]);
  if (name === 'P2PAPI_InitialWithServer') values[0].detail = safeString(args[0]);
  if (name === 'P2PAPI_CreateInstance') values[0].detail = structureShape(args[0].add(4), 0x1c8);
  if (name === 'dev_TransCGI') values[1].detail = safeString(args[1]);
  if (name === 'dev_put_auth') {
    values[1].detail = safeString(args[1]);
    values[2].detail = safeString(args[2]);
  }
  if (name === 'dev_put_IP') {
    values[1].detail = safeString(args[1]);
    values[2].detail = safeString(args[2]);
  }
  if (name === 'dev_put_devname') values[1].detail = safeString(args[1]);
  return values;
}

function install(module) {
  const names = targets[module.name.toLowerCase()];
  if (!names) return;
  for (const name of names) {
    let address = null;
    try { address = module.getExportByName(name); } catch (_) { continue; }
    const key = module.name.toLowerCase() + '!' + name;
    if (hooked.has(key)) continue;
    hooked.add(key);
    Interceptor.attach(address, {
      onEnter(args) {
        this.callId = nextCall++;
        send({event: 'enter', call: this.callId, module: module.name, function: name,
              thread: Process.getCurrentThreadId(), args: describe(name, args)});
      },
      onLeave(retval) {
        send({event: 'leave', call: this.callId, module: module.name, function: name,
              result: handle(retval)});
      }
    });
  }
  send({event: 'module-ready', module: module.name, hooks: names.length});
}

const initialModules = Process.enumerateModules();
for (const module of initialModules) install(module);
send({event: 'sdk-module-inventory', modules: initialModules.map(m => m.name).filter(
  name => /(?:p2p|ppcs|devdll|xq)/i.test(name)
)});
Process.attachModuleObserver({onAdded(module) { install(module); }});
send({event: 'tracer-ready'});
