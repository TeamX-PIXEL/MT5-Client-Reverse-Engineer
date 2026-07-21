# MT5 Direct Broker Connection - Complete Protocol Documentation

## Overview

This document describes the pure Python implementation of the MT5 binary protocol
for connecting directly to Exness broker servers, reverse-engineered from:
- `mt5api.dll` (20,277 disassembled functions)
- `strace` captures of live DLL connections
- `terminal64.exe` binary analysis

---

## Connection Architecture

```
┌──────────────────────┐                    ┌──────────────────────┐
│   Python Client      │                    │   Broker Server      │
│   (broker_connect.py)│                    │   (15.206.31.153:443)│
└──────────┬───────────┘                    └──────────┬───────────┘
           │                                           │
           │  ──── 1. TCP CONNECT (plain, no TLS) ────>
           │                                           │
           │  <──── 2. ACCEPT ─────────────────────────│
           │                                           │
           │  ──── 3. TAG 0: LOGIN PACKET ────────────>
           │      [header:9][xor_encoded:34]           │
           │                                           │
           │  <──── 4. TAG 0: SESSION KEY ─────────────│
           │      [header:9][xor_decoded:32]           │
           │      ⚡ THIS IS THE CHALLENGE! ⚡         │
           │                                           │
           │  ──── 5. TAG 1: PASSWORD PACKET ─────────>
           │      [header:9][xor_encoded:34]           │
           │      ⚡ THIS IS THE SOLUTION! ⚡          │
           │                                           │
           │  <──── 6. TAG 1: AUTHORIZATION ───────────│
           │      [header:9][xor_decoded:3539]         │
           │      Msg=0: DONE (connected!)             │
           │                                           │
           │  <──── 7. MARKET DATA STREAM ─────────────│
           │      (continuous data feed)               │
```

---

## The Challenge-Response Mechanism

### Step 1: Client Sends Login (TAG 0)

The client initiates the challenge-response by sending a login packet.
This is NOT a response to any challenge - it STARTS the authentication.

```python
# Login packet = 9-byte header + 34-byte XOR-encoded payload
# Total: 43 bytes
```

**34-byte raw payload structure:**

| Offset | Size | Field         | Description                          |
|--------|------|---------------|--------------------------------------|
| 0      | 1    | tick          | Current time (low byte)              |
| 1      | 1    | zero          | Always 0                             |
| 2      | 2    | build         | Protocol build (5500)                |
| 4      | 2    | version       | Protocol version (20813 = 0x514D)    |
| 6      | 8    | login         | Account number (uint64 LE)           |
| 14     | 16   | hardware_id   | MD5-based hardware fingerprint      |
| 30     | 4    | random        | Random value (uint32 LE)             |

**Hardware ID Generation (LCG + MD5):**
```python
def generate_hardware_id(user_id):
    num = user_id & 0xFFFFFFFF
    buf = bytearray(256)
    for i in range(256):
        num = (num * 214013 + 2531011) & 0xFFFFFFFF
        buf[i] = (num >> 16) & 0xFF
    md5 = hashlib.md5(bytes(buf)).digest()
    result = bytearray(md5)
    result[0] = 0
    for j in range(1, 16):
        result[0] = (result[0] + md5[j]) & 0xFF
    return bytes(result)
```

**XOR Encoding (before sending):**
```python
XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45,
                 123, 57, 8, 254, 33, 187, 65, 88])

def xor_encode(data):
    result = bytearray(data)
    prev = 0
    for i in range(len(result)):
        result[i] ^= (prev + XOR_KEY[i & 0xF]) & 0xFF
        prev = result[i]
    return bytes(result)
```

---

### Step 2: Server Sends Challenge (TAG 0 Response)

The server responds with a 32-byte session key. THIS IS THE CHALLENGE.

```python
# Server response = 9-byte header + 32-byte XOR-decoded payload
```

**32-byte session key structure (_E059):**

| Offset | Size | Field  | Description                          |
|--------|------|--------|--------------------------------------|
| 0      | 2    | _E02A  | Short value                          |
| 2      | 4    | Msg    | Status enum (0 = DONE)               |
| 6      | 2    | _E055  | Short value                          |
| 8      | 16   | **KEY**| **SESSION KEY (the challenge!)**     |
| 24     | 8    | _E057  | 4x short values                      |

**The session key at offset 8-23 is the cryptographic challenge.**
It changes every connection and must be incorporated into the password hash.

---

### Step 3: Client Solves Challenge (TAG 1)

The client MUST incorporate the session key into the password hash.
This proves the client knows the password AND received the real challenge.

**Challenge Solving - Streaming MD5:**

```python
def compute_password_hash(login, password, session_key):
    # PHASE 1: Hash the login credentials
    login_bytes = struct.pack('<Q', login)
    password_bytes = password.encode('utf-16-le')
    server_str_bytes = "MQ".encode('utf-16-le')  # MetaQuotes identifier
    
    data = login_bytes + password_bytes + server_str_bytes
    first_md5 = hashlib.md5(data).digest()
    
    # PHASE 2: Hash the challenge (session key) using Phase 1 state
    # This is STREAMING MD5 - NOT a new hash!
    # The MD5 state from Phase 1 is carried into Phase 2
    password_hash = md5_custom_init(first_md5, session_key)
    
    return password_hash
```

**Why Streaming MD5 Matters:**

Standard MD5: `MD5(A + B)` = hash of concatenated data
Streaming MD5: `MD5(A) → state → MD5(B, starting_from_state)`

The DLL uses .NET's `MD5CryptoServiceProvider` in streaming mode:
1. `Update(login + password + "MQ")` → finalize → get digest
2. `Reset(false)` → keep state, clear length counter
3. `Update(session_key)` → finalize → get final hash

This produces a different result than `MD5(login + password + "MQ" + session_key)`!

**Password packet inner payload (34 bytes):**

| Offset | Size | Field       | Description                          |
|--------|------|-------------|--------------------------------------|
| 0      | 2    | random      | Random value                         |
| 2      | 16   | **MD5_HASH**| **Solved challenge (password hash)** |
| 18     | 16   | random      | Random bytes                         |

---

### Step 4: Server Verifies Solution (TAG 1 Response)

If the solution is correct:
- Server sends `Msg = 0` (DONE) → Connection established!
- Server sends market data and account info

If the solution is wrong:
- Server sends `Msg ≠ 0` or closes connection
- Client must retry or abort

---

## Packet Structure

### 9-Byte Header (All Packets)

| Offset | Size | Field   | Description                          |
|--------|------|---------|--------------------------------------|
| 0      | 1    | tag     | Packet type (0=login, 1=password)    |
| 1      | 4    | length  | Payload length (uint32 LE)           |
| 5      | 2    | seq     | Sequence number (uint16 LE)          |
| 7      | 2    | version | Protocol version (2 = uint16 LE)     |

### XOR Chain Cipher

All payloads are XOR-encoded before sending and decoded after receiving.

**Key:** `[65, 182, 127, 88, 56, 12, 240, 45, 123, 57, 8, 254, 33, 187, 65, 88]`

**Encoding (raw → encoded):**
```python
prev = 0
for i in range(len(data)):
    data[i] ^= (prev + KEY[i & 0xF]) & 0xFF
    prev = data[i]  # Chain: next byte uses THIS encoded byte
```

**Decoding (encoded → raw):**
```python
prev = 0
for i in range(len(data)):
    temp = data[i]
    data[i] ^= (prev + KEY[i & 0xF]) & 0xFF
    prev = temp  # Chain: use ORIGINAL byte for next
```

---

## Complete Working Example

```python
import socket, struct, hashlib, random, time

# === Constants ===
LOGIN = 463558919
PASSWORD = "Trade@123"
BUILD = 5500
VERSION = 20813
SERVER_STRING = "MQ"  # MetaQuotes identifier

XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45,
                 123, 57, 8, 254, 33, 187, 65, 88])

# === XOR Functions ===
def xor_encode(data):
    result = bytearray(data)
    prev = 0
    for i in range(len(result)):
        result[i] ^= (prev + XOR_KEY[i & 0xF]) & 0xFF
        prev = result[i]
    return bytes(result)

def xor_decode(data):
    result = bytearray(data)
    prev = 0
    for i in range(len(result)):
        temp = result[i]
        result[i] ^= (prev + XOR_KEY[i & 0xF]) & 0xFF
        prev = temp
    return bytes(result)

# === Hardware ID ===
def generate_hardware_id(user_id):
    num = user_id & 0xFFFFFFFF
    buf = bytearray(256)
    for i in range(256):
        num = (num * 214013 + 2531011) & 0xFFFFFFFF
        buf[i] = (num >> 16) & 0xFF
    md5 = hashlib.md5(bytes(buf)).digest()
    result = bytearray(md5)
    result[0] = 0
    for j in range(1, 16):
        result[0] = (result[0] + md5[j]) & 0xFF
    return bytes(result)

# === Streaming MD5 (Challenge Solver) ===
def md5_custom_init(init_digest, data):
    """MD5 with custom initial state = streaming MD5"""
    import math
    def _f(x, y, z): return (x & y) | ((~x) & z)
    def _g(x, y, z): return (x & z) | (y & (~z))
    def _h(x, y, z): return x ^ y ^ z
    def _i(x, y, z): return y ^ (x | (~z))
    def _rol(x, n): return ((x << n) | (x >> (32-n))) & 0xFFFFFFFF
    
    T = [int(2**32 * abs(math.sin(i+1))) & 0xFFFFFFFF for i in range(64)]
    shifts = [7,12,17,22]*4 + [5,9,14,20]*4 + [4,11,16,23]*4 + [6,10,15,21]*4
    M_idx = (list(range(16)) + [(5*i+1)%16 for i in range(16)] +
             [(3*i+5)%16 for i in range(16)] + [(7*i)%16 for i in range(16)])
    funcs = [_f]*16 + [_g]*16 + [_h]*16 + [_i]*16
    
    a, b, c, d = struct.unpack('<4I', init_digest)
    msg = bytearray(data)
    orig_len = len(data) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += struct.pack('<Q', orig_len)
    
    for offset in range(0, len(msg), 64):
        X = list(struct.unpack('<16I', msg[offset:offset+64]))
        AA, BB, CC, DD = a, b, c, d
        for j in range(64):
            f_val = funcs[j](BB, CC, DD)
            g = M_idx[j]
            temp = (AA + f_val + X[g] + T[j]) & 0xFFFFFFFF
            temp = _rol(temp, shifts[j])
            temp = (temp + BB) & 0xFFFFFFFF
            AA = DD; DD = CC; CC = BB; BB = temp
        a = (a + AA) & 0xFFFFFFFF; b = (b + BB) & 0xFFFFFFFF
        c = (c + CC) & 0xFFFFFFFF; d = (d + DD) & 0xFFFFFFFF
    return struct.pack('<4I', a, b, c, d)

def solve_challenge(login, password, session_key):
    """
    THE CHALLENGE SOLVER:
    1. MD5(LE(login) + unicode(password) + "MQ")
    2. Continue with session_key (streaming MD5)
    """
    data = (struct.pack('<Q', login) +
            password.encode('utf-16-le') +
            SERVER_STRING.encode('utf-16-le'))
    first_md5 = hashlib.md5(data).digest()
    return md5_custom_init(first_md5, session_key)

# === Packet Building ===
def make_header(tag, payload_len, seq):
    return struct.pack('<B I H H', tag, payload_len, seq, 2)

def build_login_packet(login, hw_id):
    payload = bytearray(34)
    payload[0] = int(time.time()) & 0xFF
    struct.pack_into('<H', payload, 2, BUILD)
    struct.pack_into('<H', payload, 4, VERSION)
    struct.pack_into('<Q', payload, 6, login)
    payload[14:30] = hw_id[:16]
    struct.pack_into('<I', payload, 30, random.randint(0, 0xFFFFFFFF))
    return make_header(0, len(xor_encode(bytes(payload))), 0) + xor_encode(bytes(payload))

def build_password_packet(login, password, session_key):
    password_hash = solve_challenge(login, password, session_key)
    inner = bytearray(34)
    struct.pack_into('<H', inner, 0, random.randint(0, 0xFFFF))
    inner[2:18] = password_hash
    inner[18:34] = bytes([random.randint(0, 254) for _ in range(16)])
    return make_header(1, len(xor_encode(bytes(inner))), 1) + xor_encode(bytes(inner))

# === Connection ===
def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed")
        buf.extend(chunk)
    return bytes(buf)

def recv_packet(sock):
    header = recv_exact(sock, 9)
    tag = header[0]
    length = struct.unpack_from('<I', header, 1)[0]
    payload = xor_decode(recv_exact(sock, length))
    return tag, payload

# === Main ===
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
sock.settimeout(10)
sock.connect(("15.206.31.153", 443))
print("[+] Connected")

hw_id = generate_hardware_id(LOGIN)
sock.sendall(build_login_packet(LOGIN, hw_id))
print("[+] Login sent")

tag, session_key_resp = recv_packet(sock)
session_key = session_key_resp[8:24]  # Extract challenge
print(f"[+] Challenge received: {session_key.hex()}")

solution = solve_challenge(LOGIN, PASSWORD, session_key)
print(f"[+] Solution computed:  {solution.hex()}")

sock.sendall(build_password_packet(LOGIN, PASSWORD, session_key))
print("[+] Solution sent")

tag, auth_resp = recv_packet(sock)
msg = struct.unpack_from('<i', auth_resp, 4)[0]
print(f"[+] Auth status: {msg} ({'DONE' if msg == 0 else 'FAILED'})")
```

---

## Key Discoveries

1. **No TLS**: Connection is plain TCP on port 443
2. **No WebSocket**: Binary protocol over raw TCP
3. **XOR Chain Cipher**: 16-byte key with chaining (not simple XOR)
4. **Streaming MD5**: Password hash continues from previous MD5 state
5. **Challenge-Response**: Session key from server is hashed into password
6. **Server String "MQ"**: MetaQuotes identifier in password hash data

---

## Security Implications

- The password is never sent in plaintext
- The session key prevents replay attacks
- The hardware ID ties the connection to a specific machine
- The XOR cipher provides basic obfuscation (not strong encryption)
- The streaming MD5 prevents simple hash table attacks

---

## File Locations

- `broker_connect.py` - Complete working implementation
- `main.py` - Original DLL-based implementation (for reference)
- `decompiled_full/-.cs` - Full DLL decompilation (21,933 lines)
- `mt5/functions/` - 20,277 disassembled functions
- `/tmp/mt5_full.log` - Strace capture of live connection

---

## Last Updated

July 13, 2026
