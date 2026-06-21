"""
Run as Administrator to test SetTcpEntry.
This script checks if we can kill Valve TCP connections.
"""
import ctypes, socket, struct, sys

print("Is admin:", ctypes.windll.shell32.IsUserAnAdmin())

class MIB_TCPROW(ctypes.Structure):
    _fields_ = [
        ("dwState",      ctypes.c_ulong),
        ("dwLocalAddr",  ctypes.c_ulong),
        ("dwLocalPort",  ctypes.c_ulong),
        ("dwRemoteAddr", ctypes.c_ulong),
        ("dwRemotePort", ctypes.c_ulong),
    ]

try:
    import psutil
    conns = psutil.net_connections(kind="tcp4")
    valve = [c for c in conns if c.status=="ESTABLISHED" and c.raddr and
             any(c.raddr.ip.startswith(p) for p in
                 ["162.254.19","155.133.2","205.196.6","208.64.2",
                  "205.185.19","146.66.1","45.121.18","185.25.18"])]
    print(f"Live Valve connections: {len(valve)}")
    for c in valve:
        print(f"  {c.laddr} -> {c.raddr}")
        row = MIB_TCPROW()
        row.dwState      = 12
        row.dwLocalAddr  = struct.unpack("<I", socket.inet_aton(c.laddr.ip))[0]
        row.dwLocalPort  = socket.htons(c.laddr.port)
        row.dwRemoteAddr = struct.unpack("<I", socket.inet_aton(c.raddr.ip))[0]
        row.dwRemotePort = socket.htons(c.raddr.port)
        rc = ctypes.windll.iphlpapi.SetTcpEntry(ctypes.byref(row))
        print(f"  SetTcpEntry rc={rc} {'SUCCESS' if rc==0 else 'FAILED (317=need admin, 5=access denied)'}")
except Exception as e:
    print("Error:", e)

input("\nPress Enter to exit...")
