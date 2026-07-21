#!/usr/bin/env python3
"""Quick hex dump to debug servers.dat structure."""
import struct
import sys

def hexdump(data, offset, length=64):
    """Print hex dump."""
    for i in range(0, length, 16):
        pos = offset + i
        chunk = data[pos:pos+16]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f'  {pos:06x}: {hex_str:<48s} {ascii_str}')

def parse_string(data, offset, max_len):
    raw = data[offset:offset + max_len]
    for i in range(0, len(raw), 2):
        if raw[i:i+2] == b'\x00\x00':
            raw = raw[:i]
            break
    return raw.decode('utf-16-le', errors='ignore')

with open(sys.argv[1], 'rb') as f:
    data = f.read()

print(f"File size: {len(data)} bytes")

# Read header
offset = 0
header_id = struct.unpack_from('<I', data, offset)[0]; offset += 4
print(f"Header ID: {header_id}")

copyright = parse_string(data, offset, 128); offset += 128
data_type = parse_string(data, offset, 32); offset += 32
file_time = struct.unpack_from('<q', data, offset)[0]; offset += 8
obj_count = struct.unpack_from('<i', data, offset)[0]; offset += 4
offset += 16 + 228 + 4 + 4  # md5 + padding + internals

print(f"Header ends at offset: {offset}")
print(f"Object count: {obj_count}")
print()

# Try to read first server
print("=== First Server (ServerInfoEx) ===")
name = parse_string(data, offset, 128); offset += 128
print(f"Name: {name}")
company = parse_string(data, offset, 256); offset += 256
print(f"Company: {company}")
offset += 4 + 4 + 4 + 4 + 4  # internal fields
addr = parse_string(data, offset, 128); offset += 128
print(f"Address: {addr}")
print(f"After ServerInfoEx, offset: {offset}")
print()

# Read access count
access_count = struct.unpack_from('<i', data, offset)[0]; offset += 4
print(f"Access count: {access_count}")
print(f"Offset after access count: {offset}")
print()

# Read first AccessRec (356 bytes)
print("=== First AccessRec ===")
server_name = parse_string(data, offset, 64); offset += 64
print(f"Server name: {server_name}")
hexdump(data, offset, 32)
offset += 128  # internal
port = struct.unpack_from('<i', data, offset)[0]; offset += 4
print(f"Port: {port}")
hexdump(data, offset, 32)
offset += 156  # padding
print(f"After AccessRec, offset: {offset}")

# Read address count
addr_count = struct.unpack_from('<i', data, offset)[0]; offset += 4
print(f"Address count: {addr_count}")
print(f"Offset after addr count: {offset}")

# Read first AddressRec (148 bytes)
print("=== First AddressRec ===")
addr_str = parse_string(data, offset, 128); offset += 128
print(f"Address: {addr_str}")
port2 = struct.unpack_from('<i', data, offset)[0]; offset += 4
print(f"Port: {port2}")
type_val = struct.unpack_from('<i', data, offset)[0]; offset += 4
print(f"Type: {type_val}")
offset += 4 + 4 + 4  # reserved fields
print(f"After first AddressRec, offset: {offset}")

# Check remaining addresses
remaining_addrs = addr_count - 1
if remaining_addrs > 0:
    print(f"\n=== Remaining {remaining_addrs} AddressRecs ===")
    for i in range(remaining_addrs):
        addr_str = parse_string(data, offset, 128); offset += 128
        port2 = struct.unpack_from('<i', data, offset)[0]; offset += 4
        type_val = struct.unpack_from('<i', data, offset)[0]; offset += 4
        offset += 4 + 4 + 4
        print(f"  Address {i+1}: {addr_str}:{port2} (type={type_val})")

print(f"\nAfter all AccessRecs, offset: {offset}")
print(f"Remaining bytes: {len(data) - offset}")

# Check next access count
if offset < len(data):
    next_val = struct.unpack_from('<i', data, offset)[0]
    print(f"Next int32 at offset: {next_val}")
