#!/usr/bin/env python3
"""
Parse MT5 servers.dat binary file.
The file has: header (not encrypted) + encrypted records.
Counts are read from encrypted stream (not decrypted), records are decrypted individually.

Usage:
    python parse_servers_dat.py <path_to_servers.dat>
"""

import struct
import sys
import os


# XOR key (same as WebSocket protocol)
XOR_KEY = bytes([65, 182, 127, 88, 56, 12, 240, 45, 123, 57, 8, 254, 33, 187, 65, 88])


def xor_decrypt_block(data):
    """XOR chain decrypt a block (each block starts with prev=0)."""
    result = bytearray(data)
    prev = 0
    for i in range(len(result)):
        current = result[i]
        result[i] ^= ((prev + XOR_KEY[i & 0xF]) & 0xFF)
        prev = current
    return bytes(result)


class BinaryReader:
    """Read binary data from encrypted stream."""
    def __init__(self, data):
        self.data = data
        self.pos = 0
    
    def read(self, n):
        """Read n bytes (NOT decrypted)."""
        result = self.data[self.pos:self.pos+n]
        self.pos += n
        return result
    
    def read_int32(self):
        """Read int32 (NOT decrypted)."""
        val = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return val
    
    def read_encrypted(self, n):
        """Read n bytes and decrypt."""
        block = self.data[self.pos:self.pos+n]
        self.pos += n
        return xor_decrypt_block(block)
    
    def read_string(self, max_len):
        """Read encrypted string."""
        raw = self.read_encrypted(max_len)
        # Find null terminator
        for i in range(0, len(raw), 2):
            if raw[i:i+2] == b'\x00\x00':
                raw = raw[:i]
                break
        return raw.decode('utf-16-le', errors='ignore')
    
    def read_encrypted_int32(self):
        """Read encrypted int32."""
        raw = self.read_encrypted(4)
        return struct.unpack_from('<i', raw)[0]


def _read_utf16_string(data, offset, max_bytes):
    """Read a UTF-16LE string from data, stopping at null terminator."""
    raw = data[offset:offset+max_bytes]
    # Find first null terminator (2 bytes of 0x00)
    for i in range(0, len(raw), 2):
        if raw[i:i+2] == b'\x00\x00':
            raw = raw[:i]
            break
    return raw.decode('utf-16-le', errors='ignore')


def parse_servers_dat(filepath):
    """Parse servers.dat and return list of servers."""
    with open(filepath, 'rb') as f:
        data = f.read()
    
    reader = BinaryReader(data)
    
    # Header (428 bytes) - NOT encrypted
    header_id = reader.read_int32()
    copyright = _read_utf16_string(data, 4, 128)
    reader.pos = 4 + 128 + 32 + 8 + 4 + 16 + 228 + 4 + 4  # Skip to end of header
    
    print(f"Header ID: {header_id}")
    print(f"Copyright: {copyright}")
    print(f"Object count at offset {reader.pos}: {reader.read_int32()}")
    
    # Read object count
    reader.pos = 4 + 128 + 32 + 8  # Offset of obj_count
    obj_count = reader.read_int32()
    reader.pos = 428  # Skip past header
    
    print(f"Object Count: {obj_count}")
    print()
    
    servers = []
    
    for i in range(obj_count):
        server = {'index': i}
        
        if header_id in (505, 506):
            # ServerInfoEx (1716 bytes) - encrypted
            server_info = reader.read_encrypted(1716)
            server['name'] = _read_utf16_string(server_info, 0, 128)
            server['company'] = _read_utf16_string(server_info, 128, 256)
            server['dst'] = struct.unpack_from('<i', server_info, 392)[0]
            server['timezone'] = struct.unpack_from('<i', server_info, 396)[0]
            server['address'] = _read_utf16_string(server_info, 404, 128)
            server['ping_time'] = struct.unpack_from('<i', server_info, 532)[0]
            
            # AccessRec count (NOT encrypted)
            access_count = reader.read_int32()
            server['accesses'] = []
            for _ in range(access_count):
                access = {}
                # AccessRec (356 bytes) - encrypted
                access_data = reader.read_encrypted(356)
                access['server_name'] = _read_utf16_string(access_data, 0, 64)
                access['port'] = struct.unpack_from('<i', access_data, 192)[0]
                
                # AddressRec count (NOT encrypted)
                addr_count = reader.read_int32()
                access['addresses'] = []
                for _ in range(addr_count):
                    # AddressRec (148 bytes) - encrypted
                    addr_data = reader.read_encrypted(148)
                    addr = {}
                    addr['address'] = _read_utf16_string(addr_data, 0, 128)
                    access['addresses'].append(addr)
                
                server['accesses'].append(access)
            
            # AccessRecEx count (NOT encrypted)
            accessex_count = reader.read_int32()
            server['accesses_ex'] = []
            for _ in range(accessex_count):
                accessex = {}
                # AccessRecEx (3160 bytes) - encrypted
                accessex_data = reader.read_encrypted(3160)
                accessex['name1'] = _read_utf16_string(accessex_data, 0, 128)
                accessex['name2'] = _read_utf16_string(accessex_data, 128, 128)
                accessex['name3'] = _read_utf16_string(accessex_data, 256, 256)
                accessex['server_name'] = _read_utf16_string(accessex_data, 512, 64)
                accessex['host'] = _read_utf16_string(accessex_data, 600, 256)
                accessex['path'] = _read_utf16_string(accessex_data, 856, 2048)
                
                # AddressRecEx count (NOT encrypted)
                addrex_count = reader.read_int32()
                accessex['addresses'] = []
                for _ in range(addrex_count):
                    # AddressRecEx (1284 bytes) - encrypted
                    addr_data = reader.read_encrypted(1284)
                    addr = {}
                    addr['type'] = struct.unpack_from('<i', addr_data, 0)[0]
                    addr['address'] = _read_utf16_string(addr_data, 4, 512)
                    addr['description'] = _read_utf16_string(addr_data, 516, 512)
                    accessex['addresses'].append(addr)
                
                server['accesses_ex'].append(accessex)
        
        elif header_id in (503, 504):
            # ServerInfo (660 bytes) - encrypted
            server_info = reader.read_encrypted(660)
            server['name'] = _read_utf16_string(server_info, 0, 128)
            server['company'] = _read_utf16_string(server_info, 128, 256)
            server['dst'] = struct.unpack_from('<i', server_info, 392)[0]
            server['timezone'] = struct.unpack_from('<i', server_info, 396)[0]
            server['address'] = _read_utf16_string(server_info, 404, 128)
            server['ping_time'] = struct.unpack_from('<i', server_info, 532)[0]
            
            # AccessRec count (NOT encrypted)
            access_count = reader.read_int32()
            server['accesses'] = []
            for _ in range(access_count):
                access = {}
                # AccessRec (356 bytes) - encrypted
                access_data = reader.read_encrypted(356)
                access['server_name'] = _read_utf16_string(access_data, 0, 64)
                access['port'] = struct.unpack_from('<i', access_data, 192)[0]
                
                # AddressRec count (NOT encrypted)
                addr_count = reader.read_int32()
                access['addresses'] = []
                for _ in range(addr_count):
                    # AddressRec (148 bytes) - encrypted
                    addr_data = reader.read_encrypted(148)
                    addr = {}
                    addr['address'] = _read_utf16_string(addr_data, 0, 128)
                    access['addresses'].append(addr)
                
                server['accesses'].append(access)
        
        else:
            print(f"Unknown header ID: {header_id}")
            break
        
        servers.append(server)
    
    return servers


def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_servers_dat.py <path_to_servers.dat>")
        sys.exit(1)
    
    filepath = sys.argv[1]
    
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)
    
    print(f"Parsing: {filepath}")
    print(f"File size: {os.path.getsize(filepath)} bytes")
    print()
    
    servers = parse_servers_dat(filepath)
    
    print(f"\nFound {len(servers)} servers:")
    print()
    print(f"{'#':<5} {'Server Name':<35} {'Company':<30}")
    print("-" * 70)
    
    for s in servers:
        print(f"{s['index']:<5} {s['name']:<35} {s.get('company', '')[:30]:<30}")
    
    # Filter for Exness servers
    exness_servers = [s for s in servers if 'exness' in s['name'].lower() or 'exness' in s.get('company', '').lower()]
    
    if exness_servers:
        print()
        print(f"=== Exness Servers ({len(exness_servers)}) ===")
        print()
        for s in exness_servers:
            print(f"\n  Server: {s['name']}")
            print(f"  Company: {s.get('company', 'N/A')}")
            
            for a in s.get('accesses', []):
                for addr in a.get('addresses', []):
                    print(f"    - {addr['address']}")
            
            for a in s.get('accesses_ex', []):
                for addr in a.get('addresses', []):
                    print(f"    - {addr['address']}")


if __name__ == '__main__':
    main()
