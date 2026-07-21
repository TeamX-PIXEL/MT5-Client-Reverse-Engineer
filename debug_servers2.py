#!/usr/bin/env python3
"""Debug servers.dat with XOR decryption."""
import struct
import sys

XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45, 123, 57, 8, 254, 33, 187, 65, 88])

def xor_decrypt(data):
    result = bytearray(data)
    prev = 0
    for i in range(len(result)):
        current = result[i]
        result[i] ^= ((prev + XOR_KEY[i & 0xF]) & 0xFF)
        prev = current
    return bytes(result)

def parse_string(data, offset, max_len):
    raw = data[offset:offset + max_len]
    for i in range(0, len(raw), 2):
        if raw[i:i+2] == b'\x00\x00':
            raw = raw[:i]
            break
    return raw.decode('utf-16-le', errors='ignore')

def hexdump(data, offset, length=64):
    for i in range(0, length, 16):
        pos = offset + i
        chunk = data[pos:pos+16]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f'  {pos:06x}: {hex_str:<48s} {ascii_str}')

with open(sys.argv[1], 'rb') as f:
    data = f.read()

# Header
offset = 0
header_id = struct.unpack_from('<I', data, offset)[0]; offset += 4
print(f"Header ID: {header_id}")
offset += 128 + 32 + 8 + 4 + 16 + 228 + 4 + 4  # rest of header

# Decrypt
encrypted = data[offset:]
decrypted = xor_decrypt(encrypted)
print(f"Encrypted size: {len(encrypted)}")
print(f"Decrypted size: {len(decrypted)}")
print()

# Try first server
pos = 0
name = parse_string(decrypted, pos, 128); pos += 128
print(f"Name: {name}")
company = parse_string(decrypted, pos, 256); pos += 256
print(f"Company: {company[:50]}...")
print(f"Pos after name+company: {pos}")

# Hex dump around pos
print("Hex dump around pos:")
hexdump(decrypted, pos - 16, 64)

# Skip internal fields
pos += 4 + 4 + 4 + 4 + 4
addr = parse_string(decrypted, pos, 128); pos += 128
print(f"Address field: {addr[:50]}...")
print(f"Pos after address: {pos}")

# Access count
access_count = struct.unpack_from('<i', decrypted, pos)[0]; pos += 4
print(f"Access count: {access_count}")
print(f"Pos after access count: {pos}")

# Hex dump around current position
print("Hex dump around current position:")
hexdump(decrypted, pos, 64)

# Read first AccessRec
print("\n=== First AccessRec ===")
server_name = parse_string(decrypted, pos, 64); pos += 64
print(f"Server name: {server_name}")
print(f"Pos after server name: {pos}")

# Internal byte array
hexdump(decrypted, pos, 32)
pos += 128
print(f"Pos after internal: {pos}")

port = struct.unpack_from('<i', decrypted, pos)[0]; pos += 4
print(f"Port: {port}")
hexdump(decrypted, pos, 32)
pos += 156
print(f"Pos after padding: {pos}")

# Address count
addr_count = struct.unpack_from('<i', decrypted, pos)[0]; pos += 4
print(f"Address count: {addr_count}")
print(f"Pos after addr count: {pos}")

# Read first AddressRec
print("\n=== First AddressRec ===")
addr_str = parse_string(decrypted, pos, 128); pos += 128
print(f"Address: {addr_str}")
port2 = struct.unpack_from('<i', decrypted, pos)[0]; pos += 4
print(f"Port: {port2}")
type_val = struct.unpack_from('<i', decrypted, pos)[0]; pos += 4
print(f"Type: {type_val}")
pos += 4 + 4 + 4
print(f"Pos after first AddressRec: {pos}")
