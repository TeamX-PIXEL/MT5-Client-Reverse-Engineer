#!/usr/bin/env python3
"""Debug - check if servers.dat is encrypted."""
import struct
import sys

with open(sys.argv[1], 'rb') as f:
    data = f.read()

# Header
offset = 0
header_id = struct.unpack_from('<I', data, offset)[0]; offset += 4
print(f"Header ID: {header_id}")

# Skip to after header
offset += 128 + 32 + 8 + 4 + 16 + 228 + 4 + 4

# Read first server name (128 bytes)
print("\n=== Raw data (no decryption) ===")
print("First 128 bytes (should be server name):")
raw_name = data[offset:offset+128]
print(f"Hex: {raw_name[:64].hex()}")
print(f"UTF-16: {raw_name.decode('utf-16-le', errors='ignore')[:64]}")

# Check if it looks like encrypted data
print(f"\nFirst byte: {data[offset]:02x}")
print(f"Is it zero? {data[offset] == 0}")
print(f"Is it printable ASCII? {32 <= data[offset] < 127}")

# Try reading without decryption
print("\n=== Reading without decryption ===")
pos = offset
name = data[pos:pos+128].decode('utf-16-le', errors='ignore').rstrip('\x00')
pos += 128
company = data[pos:pos+256].decode('utf-16-le', errors='ignore').rstrip('\x00')
pos += 256
print(f"Name: {name}")
print(f"Company: {company[:50]}...")
print(f"Pos: {pos}")

# Skip internal fields
pos += 4 + 4 + 4 + 4 + 4
addr = data[pos:pos+128].decode('utf-16-le', errors='ignore').rstrip('\x00')
pos += 128
print(f"Address: {addr}")

# Access count
access_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
print(f"Access count: {access_count}")
print(f"Pos: {pos}")

# Read AccessRec
print("\n=== AccessRec (no decryption) ===")
server_name = data[pos:pos+64].decode('utf-16-le', errors='ignore').rstrip('\x00')
pos += 64
print(f"Server name: {server_name}")
pos += 128  # internal
port = struct.unpack_from('<i', data, pos)[0]; pos += 4
print(f"Port: {port}")
pos += 156  # padding
print(f"Pos after AccessRec: {pos}")

# Address count
addr_count = struct.unpack_from('<i', data, pos)[0]; pos += 4
print(f"Address count: {addr_count}")
print(f"Pos: {pos}")

# Read AddressRec
addr_str = data[pos:pos+128].decode('utf-16-le', errors='ignore').rstrip('\x00')
pos += 128
port2 = struct.unpack_from('<i', data, pos)[0]; pos += 4
type_val = struct.unpack_from('<i', data, pos)[0]; pos += 4
pos += 4 + 4 + 4
print(f"Address: {addr_str}")
print(f"Port: {port2}")
print(f"Type: {type_val}")
print(f"Pos after AddressRec: {pos}")
