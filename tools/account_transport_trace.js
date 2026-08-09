'use strict';

// Observe only endpoint and packet-shape metadata. Payload bytes, account
// identifiers, device identifiers, credentials, and TLS key material are never
// emitted to the host.
const socketEndpoints = new Map();
const installed = new Set();

function readUnicode(pointer) {
  if (pointer.isNull() || pointer.compare(ptr('0x10000')) < 0) return null;
  try { return pointer.readUtf16String(8192); } catch (_) { return null; }
}

function responseShape(storage) {
  try {
    const pointer = storage.readPointer();
    if (pointer.isNull()) return null;
    const candidates = [];
    try { candidates.push(pointer.readUtf16String(8192)); } catch (_) {}
    try { candidates.push(pointer.readCString(16384)); } catch (_) {}
    for (const candidate of candidates) {
      if (candidate.length > 16384) continue;
      try {
        return {encoding: 'json', schema: valueShape(JSON.parse(candidate.trim()), 0)};
      } catch (_) {}
    }
    return {encoding: 'unknown', schema: null};
  } catch (_) {
    return null;
  }
}

function valueShape(value, depth) {
  if (depth > 4) return 'nested';
  if (value === null) return 'null';
  if (Array.isArray(value)) {
    return {type: 'array', length: value.length,
            item: value.length ? valueShape(value[0], depth + 1) : 'empty'};
  }
  if (typeof value === 'object') {
    const fields = {};
    for (const key of Object.keys(value).sort()) fields[key] = valueShape(value[key], depth + 1);
    return {type: 'object', fields: fields};
  }
  return typeof value;
}

function textShape(text) {
  if (text === null) return null;
  const trimmed = text.trim();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try { return {encoding: 'json', schema: valueShape(JSON.parse(trimmed), 0)}; } catch (_) {}
  }
  let path = trimmed;
  let parameters = '';
  const query = trimmed.indexOf('?');
  if (query >= 0) {
    path = trimmed.substring(0, query);
    parameters = trimmed.substring(query + 1);
  } else if (trimmed.includes('=') || trimmed.includes('\n')) {
    path = 'parameter-body';
    parameters = trimmed;
  }
  const fields = [];
  for (const part of parameters.split(/(?:&|\r?\n)/)) {
    const separator = part.indexOf('=');
    if (separator <= 0) continue;
    const key = part.substring(0, separator).trim();
    const value = part.substring(separator + 1).trim();
    if (!/^[A-Za-z0-9_./-]{1,64}$/.test(key)) continue;
    const field = {key: key, valueLength: value.length,
                   valueKind: /^-?[0-9]+$/.test(value) ? 'integer' : 'string'};
    if (key === 'oemid' || key === 'type') field.safeConstant = value;
    fields.push(field);
  }
  return {encoding: 'parameters', path: path, fields: fields};
}

function readPort(address) {
  return (address.add(2).readU8() << 8) | address.add(3).readU8();
}

function describeAddress(address) {
  try {
    const family = address.readU16();
    const port = readPort(address);
    if (family === 2) {
      const parts = [];
      for (let index = 4; index < 8; index++) parts.push(address.add(index).readU8());
      return {family: 'ipv4', address: parts.join('.'), port: port};
    }
    if (family === 23) return {family: 'ipv6', address: 'redacted-ipv6', port: port};
    return {family: 'other', port: port};
  } catch (_) {
    return {family: 'unreadable'};
  }
}

function isLoopback(endpoint) {
  return endpoint.address === '127.0.0.1' || endpoint.address === '0.0.0.0';
}

function packetShape(buffer, length) {
  const sampleLength = Math.min(length, 16);
  let printable = 0;
  const bytes = [];
  try {
    for (let index = 0; index < sampleLength; index++) {
      const value = buffer.add(index).readU8();
      bytes.push(value);
      if (value >= 0x20 && value <= 0x7e) printable++;
    }
  } catch (_) {
    return {length: length, kind: 'unreadable'};
  }
  let kind = 'binary';
  if (bytes.length >= 3 && bytes[0] >= 0x14 && bytes[0] <= 0x17 && bytes[1] === 3) {
    kind = 'tls-record';
  } else if (bytes.length && printable / bytes.length >= 0.8) {
    kind = 'printable';
  }
  return {length: length, kind: kind};
}

function installExport(moduleName, exportName, callbacks) {
  const key = moduleName.toLowerCase() + '!' + exportName;
  if (installed.has(key)) return;
  const module = Process.findModuleByName(moduleName);
  if (module === null) return;
  let address;
  try { address = module.getExportByName(exportName); } catch (_) { return; }
  installed.add(key);
  Interceptor.attach(address, callbacks);
}

function installWinsock() {
  for (const name of ['connect', 'WSAConnect']) {
    installExport('ws2_32.dll', name, {
      onEnter(args) {
        this.socket = args[0].toString();
        this.endpoint = describeAddress(args[1]);
      },
      onLeave(result) {
        if (result.toInt32() === 0 && !isLoopback(this.endpoint)) {
          socketEndpoints.set(this.socket, this.endpoint);
          send({event: 'connect', endpoint: this.endpoint});
        }
      }
    });
  }
  installExport('ws2_32.dll', 'send', {
    onEnter(args) {
      const endpoint = socketEndpoints.get(args[0].toString());
      const length = args[2].toInt32();
      if (endpoint !== undefined && length > 0) {
        send({event: 'send-shape', endpoint: endpoint, packet: packetShape(args[1], length)});
      }
    }
  });
  installExport('ws2_32.dll', 'getaddrinfo', {
    onEnter(args) {
      try {
        if (!args[0].isNull()) send({event: 'dns', hostname: args[0].readCString(255)});
      } catch (_) {}
    }
  });
  installExport('ws2_32.dll', 'GetAddrInfoW', {
    onEnter(args) {
      try {
        if (!args[0].isNull()) send({event: 'dns', hostname: args[0].readUtf16String(255)});
      } catch (_) {}
    }
  });
  installExport('ws2_32.dll', 'gethostbyname', {
    onEnter(args) {
      try {
        if (!args[0].isNull()) send({event: 'dns', hostname: args[0].readCString(255)});
      } catch (_) {}
    }
  });
}

function installAccountApi() {
  const module = Process.mainModule;
  const methods = [
    {name: 'request', rva: 0x4f0ed8, bodyInEcx: false},
    {name: 'post', rva: 0x4f110c, bodyInEcx: true},
    {name: 'post-json', rva: 0x4f12dc, bodyInEcx: true}
  ];
  for (const method of methods) {
    const key = 'main!' + method.name;
    if (installed.has(key)) continue;
    installed.add(key);
    Interceptor.attach(module.base.add(method.rva), {
      onEnter(_args) {
        this.output = this.context.sp.add(Process.pointerSize).readPointer();
        send({event: 'account-api-call', method: method.name,
              endpoint: textShape(readUnicode(this.context.edx)),
              body: method.bodyInEcx ? textShape(readUnicode(this.context.ecx)) : null});
      },
      onLeave(result) {
        send({event: 'account-api-result', method: method.name,
              status: result.toInt32(), response: responseShape(this.output)});
      }
    });
  }
}

installWinsock();
installAccountApi();
Process.attachModuleObserver({onAdded(_module) { installWinsock(); }});
send({event: 'transport-tracer-ready'});
