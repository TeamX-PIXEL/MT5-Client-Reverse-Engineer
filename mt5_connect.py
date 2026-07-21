#!/usr/bin/env python3
"""
MT5 Client - Connect by server name
Automatically resolves server name (e.g. "Exness-MT5Trial16") to IP address
using the local servers.dat file.

Usage:
    python mt5_connect.py <login> <password> <server_name>

Example:
    python mt5_connect.py 463558919 "Trade@123" "Exness-MT5Trial17"
"""

import sys
import os
from parse_servers_dat import parse_servers_dat


def find_server_ip(server_name, servers_dat_path=None):
    """
    Find server IP address by server name.
    Tries online APIs (SearchMQ + Search) first, falls back to local servers.dat.
    
    Args:
        server_name: Server name (e.g. "Exness-MT5Trial17")
        servers_dat_path: Path to servers.dat file. If None, searches default locations.
    
    Returns:
        List of IP:port strings (e.g. ["13.213.81.113:443", "16.78.218.32:443"])
    """
    # === Method 1: Online APIs (broker_search.py) ===
    try:
        from broker_search import find_server
        info = find_server(server_name)
        if info:
            print(f"Found via online API: {info['name']} ({info['company']})")
            ips = info['access']
            print(f"IP addresses: {', '.join(ips[:5])}")
            return ips
    except Exception:
        pass

    # === Method 2: Local servers.dat ===
    default_paths = [
        servers_dat_path,
        "/home/teamx/.wine/drive_c/Program Files/MetaTrader 5 EXNESS/Config/servers.dat",
        os.path.expanduser("~/.wine/drive_c/Program Files/MetaTrader 5 EXNESS/Config/servers.dat"),
        os.path.expanduser("~/AppData/Roaming/MetaQuotes/Terminal/*/config/servers.dat"),
    ]
    
    filepath = None
    for path in default_paths:
        if path and os.path.exists(path):
            filepath = path
            break
    
    if not filepath:
        import glob
        for path in default_paths:
            if path and '*' in path:
                matches = glob.glob(path)
                if matches:
                    filepath = matches[0]
                    break
    
    if not filepath:
        print(f"Error: servers.dat not found")
        return None
    
    print(f"Using local servers.dat: {filepath}")
    servers = parse_servers_dat(filepath)
    
    for s in servers:
        if s['name'].lower() == server_name.lower():
            ips = []
            for a in s.get('accesses', []):
                for addr in a.get('addresses', []):
                    ips.append(addr['address'])
            for a in s.get('accesses_ex', []):
                for addr in a.get('addresses', []):
                    ips.append(addr['address'])
            
            if ips:
                print(f"Found server: {s['name']}")
                print(f"IP addresses: {', '.join(ips[:5])}")
                return ips
            else:
                print(f"Server found but no IP addresses: {s['name']}")
                return None
    
    print(f"Server not found: {server_name}")
    return None


def main():
    if len(sys.argv) < 4:
        print("Usage: python mt5_connect.py <login> <password> <server_name> [servers_dat_path]")
        print()
        print("Example:")
        print("  python mt5_connect.py 463558919 'Trade@123' 'Exness-MT5Trial17'")
        sys.exit(1)
    
    login = sys.argv[1]
    password = sys.argv[2]
    server_name = sys.argv[3]
    servers_dat_path = sys.argv[4] if len(sys.argv) > 4 else None
    
    print("=" * 60)
    print("MT5 Connection Helper")
    print("=" * 60)
    print()
    
    # Find server IP
    ips = find_server_ip(server_name, servers_dat_path)
    
    if not ips:
        sys.exit(1)
    
    print()
    print("=" * 60)
    print("Connection Details")
    print("=" * 60)
    print(f"Login: {login}")
    print(f"Server: {server_name}")
    print(f"Password: {'*' * len(password)}")
    print(f"IP addresses: {', '.join(ips[:3])}")
    print()
    print("To connect, use the first IP address with port 443:")
    print(f"  Host: {ips[0].split(':')[0]}")
    print(f"Port: {ips[0].split(':')[1] if ':' in ips[0] else '443'}")
    print()
    print("Connection code example:")
    print(f"""
from ws_client import MT5WebSocketClient

client = MT5WebSocketClient(
    login={login},
    password="{password}",
    host="{ips[0].split(':')[0]}",
    port={ips[0].split(':')[1] if ':' in ips[0] else 443}
)
client.connect()
""")


if __name__ == '__main__':
    main()
