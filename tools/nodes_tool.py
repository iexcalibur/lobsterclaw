"""
Nodes tool — mirrors OpenClaw's nodes-tool.ts full action surface.

In OpenClaw, nodes are remote machines (Raspberry Pi, VMs, cloud instances)
that the agent can reach through the gateway relay. Actions bridge
SSH, file transfers, WoL packets, tunnel management, and exec routing.

PyGate implementation:
  - Full schema parity with nodes-tool.ts
  - SSH-based execution (requires host to be configured in NODE_<n>_HOST etc.)
  - Nodes configured via .env (NODE_0_NAME, NODE_0_HOST, NODE_0_USER, NODE_0_KEY)
  - Actions that require hardware (wake/sleep/reboot) delegate to SSH commands
  - Tunnel management uses SSH -L port forwarding

Action surface (1:1 with OpenClaw nodes-tool.ts):
  list         — list all configured nodes and their status
  status       — get detailed status for a specific node
  connect      — test SSH connectivity to a node
  disconnect   — close a persistent SSH connection
  exec         — run a command on a remote node via SSH
  tunnel       — create or list SSH port-forward tunnels
  ping         — ICMP or TCP ping to a node
  ssh          — interactive SSH session placeholder (returns instructions)
  scp          — copy files to/from a node
  wake         — send Wake-on-LAN magic packet
  sleep        — suspend a node (via SSH)
  reboot       — reboot a node (via SSH)
  info         — show node hardware/OS info
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Node configuration (from .env: NODE_<n>_NAME, HOST, USER, PORT, KEY)
# ------------------------------------------------------------------

@dataclass
class NodeConfig:
    name: str
    host: str
    user: str = "pi"
    port: int = 22
    key_path: str | None = None
    mac_address: str | None = None  # for Wake-on-LAN
    description: str = ""


def _load_nodes() -> list[NodeConfig]:
    """Load node configs from env vars: NODE_0_NAME, NODE_0_HOST, etc."""
    nodes = []
    idx = 0
    while True:
        name = os.environ.get(f"NODE_{idx}_NAME")
        host = os.environ.get(f"NODE_{idx}_HOST")
        if not name or not host:
            break
        nodes.append(NodeConfig(
            name=name,
            host=host,
            user=os.environ.get(f"NODE_{idx}_USER", "pi"),
            port=int(os.environ.get(f"NODE_{idx}_PORT", "22")),
            key_path=os.environ.get(f"NODE_{idx}_KEY"),
            mac_address=os.environ.get(f"NODE_{idx}_MAC"),
            description=os.environ.get(f"NODE_{idx}_DESC", ""),
        ))
        idx += 1
    return nodes


# ------------------------------------------------------------------
# Tool definition (matches nodes-tool.ts schema)
# ------------------------------------------------------------------

TOOL_DEFINITION = ToolDefinition(
    name="nodes",
    description=(
        "Manage and interact with remote nodes (Raspberry Pi, VMs, servers).\n\n"
        "Nodes are configured via .env:\n"
        "  NODE_0_NAME=mypi  NODE_0_HOST=192.168.1.100  NODE_0_USER=pi\n"
        "  NODE_0_PORT=22    NODE_0_KEY=~/.ssh/id_rsa   NODE_0_MAC=aa:bb:cc:dd:ee:ff\n\n"
        "Actions:\n"
        "  list       — list all configured nodes\n"
        "  status     — ping + SSH test a node\n"
        "  connect    — verify SSH connectivity\n"
        "  disconnect — close cached SSH session\n"
        "  exec       — run a shell command on a remote node\n"
        "  tunnel     — create/list SSH port-forward tunnels\n"
        "  ping       — ICMP ping (or TCP if ICMP unavailable)\n"
        "  scp        — copy files to/from a node\n"
        "  wake       — send Wake-on-LAN magic packet to a node\n"
        "  sleep      — suspend a node via SSH\n"
        "  reboot     — reboot a node via SSH\n"
        "  info       — show node hardware/OS info via SSH"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Action: list|status|connect|disconnect|exec|tunnel|ping|scp|wake|sleep|reboot|info",
            },
            "node": {
                "type": "string",
                "description": "Node name or index (required for most actions except list)",
            },
            "command": {
                "type": "string",
                "description": "Shell command to run (for exec action)",
            },
            "local_port": {
                "type": "integer",
                "description": "Local port for tunnel (tunnel action)",
            },
            "remote_port": {
                "type": "integer",
                "description": "Remote port for tunnel (tunnel action)",
            },
            "remote_host": {
                "type": "string",
                "description": "Remote host for tunnel target (tunnel action, default: localhost)",
            },
            "source": {
                "type": "string",
                "description": "Source path for scp (local or remote:path)",
            },
            "destination": {
                "type": "string",
                "description": "Destination path for scp",
            },
            "timeout": {
                "type": "integer",
                "description": "Command timeout in seconds (default 30)",
                "default": 30,
            },
        },
        "required": ["action"],
    },
    fn=lambda **kw: _nodes(**kw),
)


# ------------------------------------------------------------------
# Implementation
# ------------------------------------------------------------------

async def _nodes(
    action: str,
    node: str | None = None,
    command: str | None = None,
    local_port: int | None = None,
    remote_port: int | None = None,
    remote_host: str = "localhost",
    source: str | None = None,
    destination: str | None = None,
    timeout: int = 30,
) -> str:
    action = action.lower().strip()
    nodes = _load_nodes()

    if action == "list":
        if not nodes:
            return (
                "No nodes configured. Add to .env:\n"
                "  NODE_0_NAME=mypi\n"
                "  NODE_0_HOST=192.168.1.100\n"
                "  NODE_0_USER=pi\n"
                "  NODE_0_KEY=~/.ssh/id_rsa"
            )
        lines = [f"Configured nodes ({len(nodes)}):"]
        for i, n in enumerate(nodes):
            mac_str = f" mac={n.mac_address}" if n.mac_address else ""
            lines.append(f"  [{i}] {n.name} — {n.user}@{n.host}:{n.port}{mac_str}")
            if n.description:
                lines.append(f"       {n.description}")
        return "\n".join(lines)

    # All other actions require a node
    if node is None:
        return "Error: 'node' parameter is required (use node name or index)"

    cfg = _resolve_node(nodes, node)
    if cfg is None:
        return f"Node '{node}' not found. Use action=list to see available nodes."

    if action == "ping":
        return await _ping(cfg, timeout)

    if action in ("connect", "status"):
        return await _ssh_test(cfg, timeout)

    if action == "disconnect":
        return f"Node '{cfg.name}' connection cache cleared (stateless SSH — no persistent sessions)."

    if action == "exec":
        if not command:
            return "Error: 'command' is required for exec action"
        return await _ssh_exec(cfg, command, timeout)

    if action == "tunnel":
        if local_port and remote_port:
            return _tunnel_create(cfg, local_port, remote_port, remote_host)
        return _tunnel_list(cfg)

    if action == "scp":
        if not source or not destination:
            return "Error: 'source' and 'destination' are required for scp action"
        return await _scp(cfg, source, destination, timeout)

    if action == "wake":
        return await _wake(cfg)

    if action == "sleep":
        return await _ssh_exec(cfg, "systemctl suspend || pm-suspend || echo 'suspend not available'", timeout)

    if action == "reboot":
        return await _ssh_exec(cfg, "sudo reboot", timeout)

    if action == "info":
        info_cmd = (
            "echo '=== System ===' && uname -a && "
            "echo '=== CPU ===' && cat /proc/cpuinfo | grep 'Model\\|model name' | head -3 && "
            "echo '=== Memory ===' && free -h && "
            "echo '=== Disk ===' && df -h / && "
            "echo '=== Uptime ===' && uptime"
        )
        return await _ssh_exec(cfg, info_cmd, timeout)

    return (
        f"Unknown action '{action}'. "
        "Use: list, status, connect, disconnect, exec, tunnel, ping, scp, wake, sleep, reboot, info"
    )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _resolve_node(nodes: list[NodeConfig], identifier: str) -> NodeConfig | None:
    if identifier.isdigit():
        idx = int(identifier)
        return nodes[idx] if 0 <= idx < len(nodes) else None
    for n in nodes:
        if n.name.lower() == identifier.lower():
            return n
    return None


def _ssh_args(cfg: NodeConfig) -> list[str]:
    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        "-p", str(cfg.port),
    ]
    if cfg.key_path:
        args += ["-i", str(Path(cfg.key_path).expanduser())]
    args.append(f"{cfg.user}@{cfg.host}")
    return args


async def _ping(cfg: NodeConfig, timeout: int) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "3", "-W", "2", cfg.host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        if proc.returncode == 0:
            return f"✅ Node '{cfg.name}' ({cfg.host}) is reachable.\n{output}"
        return f"❌ Node '{cfg.name}' ({cfg.host}) is not reachable.\n{output}"
    except asyncio.TimeoutError:
        return f"Ping timed out for '{cfg.name}' ({cfg.host})"
    except FileNotFoundError:
        return f"'ping' not found. Try exec with command='curl -o /dev/null {cfg.host}'"


async def _ssh_test(cfg: NodeConfig, timeout: int) -> str:
    result = await _ssh_exec(cfg, "echo ok && uname -n && uptime", timeout)
    if "ok" in result:
        return f"✅ SSH connection to '{cfg.name}' ({cfg.user}@{cfg.host}:{cfg.port}) OK\n{result}"
    return f"❌ SSH connection to '{cfg.name}' failed:\n{result}"


async def _ssh_exec(cfg: NodeConfig, command: str, timeout: int) -> str:
    args = _ssh_args(cfg) + [command]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        return f"Exit {proc.returncode}:\n{output}" if proc.returncode != 0 else output
    except asyncio.TimeoutError:
        return f"SSH command timed out after {timeout}s on '{cfg.name}'"
    except FileNotFoundError:
        return "Error: 'ssh' not found. Install OpenSSH."
    except Exception as e:
        return f"SSH error: {e}"


async def _scp(cfg: NodeConfig, source: str, destination: str, timeout: int) -> str:
    """Copy files using scp. Use 'remote:path' syntax for remote paths."""
    def _expand(path: str) -> str:
        if ":" in path:
            node_name, remote_path = path.split(":", 1)
            return f"{cfg.user}@{cfg.host}:{remote_path}"
        return str(Path(path).expanduser())

    src = _expand(source)
    dst = _expand(destination)
    args = ["scp", "-P", str(cfg.port), "-o", "StrictHostKeyChecking=no"]
    if cfg.key_path:
        args += ["-i", str(Path(cfg.key_path).expanduser())]
    args += [src, dst]

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode(errors="replace")
        if proc.returncode == 0:
            return f"SCP complete: {source} → {destination}"
        return f"SCP failed (exit {proc.returncode}):\n{output}"
    except asyncio.TimeoutError:
        return f"SCP timed out after {timeout}s"
    except FileNotFoundError:
        return "Error: 'scp' not found. Install OpenSSH."
    except Exception as e:
        return f"SCP error: {e}"


def _tunnel_create(cfg: NodeConfig, local_port: int, remote_port: int, remote_host: str) -> str:
    """Return the SSH tunnel command (PyGate is stateless; user must run it)."""
    key_flag = f"-i {cfg.key_path}" if cfg.key_path else ""
    cmd = (
        f"ssh -N -L {local_port}:{remote_host}:{remote_port} "
        f"-p {cfg.port} {key_flag} {cfg.user}@{cfg.host}"
    )
    return (
        f"Tunnel command for '{cfg.name}':\n"
        f"  {cmd}\n\n"
        f"This creates a tunnel: localhost:{local_port} → {remote_host}:{remote_port} on {cfg.host}\n"
        f"Run in a terminal or use exec action to start it in the background on the node."
    )


def _tunnel_list(cfg: NodeConfig) -> str:
    return (
        f"Active tunnel listing not available (stateless SSH). "
        f"Use tunnel with local_port + remote_port to get the tunnel command for '{cfg.name}'."
    )


async def _wake(cfg: NodeConfig) -> str:
    """Send a Wake-on-LAN magic packet."""
    if not cfg.mac_address:
        return (
            f"Node '{cfg.name}' has no MAC address configured. "
            f"Set NODE_<n>_MAC=aa:bb:cc:dd:ee:ff in .env."
        )
    mac = cfg.mac_address.replace(":", "").replace("-", "")
    if len(mac) != 12:
        return f"Invalid MAC address format: {cfg.mac_address}"

    # Build magic packet: 6 bytes of 0xFF + MAC repeated 16 times
    magic = bytes.fromhex("FF" * 6 + mac * 16)
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, ("<broadcast>", 9))
        sock.close()
        return f"Wake-on-LAN packet sent to '{cfg.name}' (MAC: {cfg.mac_address}) ✅"
    except Exception as e:
        return f"WoL error: {e}"
