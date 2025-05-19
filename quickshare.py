#!/usr/bin/env python3
import sys
import os
from jinja2 import Environment, PackageLoader, select_autoescape
import qrcode
import argparse
import socket
import netifaces
import mimetypes
import urllib.parse
from flask import Flask, render_template, send_file, abort, request, redirect, url_for
from pathlib import Path

if getattr(sys, 'frozen', False):
    # Nuitka/compiled mode
    base_path = os.path.dirname(sys.executable)
else:
    # Normal Python mode
    base_path = os.path.dirname(os.path.abspath(__file__))

template_path = os.path.join(base_path, "templates")
app = Flask(__name__, template_folder=template_path)
PORT = 8001
DEFAULT_INTERFACE = '0.0.0.0'  # Bind to all interfaces by default

# Global variables to store configuration
share_path = None
is_file_share = False

def get_ip_addresses():
    """Get all available IP addresses on the machine."""
    addresses = []
    for interface in netifaces.interfaces():
        try:
            ifaddresses = netifaces.ifaddresses(interface)
            if netifaces.AF_INET in ifaddresses:
                for link in ifaddresses[netifaces.AF_INET]:
                    if 'addr' in link and link['addr'] != '127.0.0.1':
                        addresses.append((interface, link['addr']))
        except ValueError:
            pass
    return addresses

def select_interface():
    """Let user select which interface to use for QR code."""
    addresses = get_ip_addresses()

    if not addresses:
        print("No network interfaces found.")
        return None

    print("\nAvailable network interfaces:")
    for i, (interface, ip) in enumerate(addresses):
        print(f"{i+1}. {interface}: {ip}")

    try:
        choice = int(input("\nSelect interface to use for QR code [1]: ") or "1")
        if 1 <= choice <= len(addresses):
            return addresses[choice-1][1]
        else:
            print(f"Invalid choice. Using {addresses[0][1]}")
            return addresses[0][1]
    except ValueError:
        print(f"Invalid input. Using {addresses[0][1]}")
        return addresses[0][1]

def is_previewable(content_type):
    """Determine if file can be previewed in browser."""
    previewable_types = [
        'image/', 'video/', 'audio/', 'text/',
        'application/pdf'
    ]
    return any(content_type.startswith(t) for t in previewable_types)

def generate_qr_code(url):
    """Generate QR code for the URL and display in terminal."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # Print QR code to terminal
    qr.print_ascii(invert=True)
    print(f"\nScan QR code to access: {url}")

def is_safe_path(base_path, requested_path):
    """Check if the requested path is safe (within base_path)."""
    # Get absolute paths
    base_path = os.path.abspath(base_path)
    requested_path = os.path.abspath(requested_path)

    # Check if requested_path starts with base_path
    return requested_path.startswith(base_path)

@app.route("/", methods=["GET"])
def serve_root():
    return serve_path("")

@app.route('/<path:path>')
def serve_path(path):
    global share_path, is_file_share

    # If sharing a single file, serve it directly
    if is_file_share:
        return send_file(
            share_path,
            as_attachment=True
        )

    # Normalize requested path and ensure it's within share_path
    try:
        if path:
            # Check if path is safe
            full_path = os.path.join(share_path, path)
            if not is_safe_path(share_path, full_path):
                abort(404)  # Path traversal attempt
        else:
            full_path = share_path
    except:
        abort(404)

    if not os.path.exists(full_path):
        abort(404)

    # If path is a file, serve it
    if os.path.isfile(full_path):
        content_type = mimetypes.guess_type(full_path)[0] or 'application/octet-stream'

        # Check if preview or download was requested
        download = request.args.get('download', 'false').lower() == 'true'
        can_preview = is_previewable(content_type) and not download

        if can_preview:
            # For preview, just serve the file normally
            return send_file(full_path)
        else:
            # For download, set as attachment
            return send_file(
                full_path,
                as_attachment=True
            )

    # If path is a directory, list its contents
    elif os.path.isdir(full_path):
        try:
            entries = os.listdir(full_path)
        except PermissionError:
            abort(403)

        # Sort entries: directories first, then by name
        files = []
        folders = []

        for name in entries:
            entry_path = os.path.join(full_path, name)
            rel_path = os.path.join(path, name) if path else name

            if os.path.isdir(entry_path):
                folders.append({
                    'name': name,
                    'path': rel_path,
                    'is_dir': True
                })
            else:
                content_type = mimetypes.guess_type(entry_path)[0] or 'application/octet-stream'
                files.append({
                    'name': name,
                    'path': rel_path,
                    'size': os.path.getsize(entry_path),
                    'content_type': content_type,
                    'can_preview': is_previewable(content_type)
                })

        # Sort folders and files alphabetically
        folders.sort(key=lambda x: x['name'].lower())
        files.sort(key=lambda x: x['name'].lower())

        # Get parent directory path
        parent_path = os.path.dirname(path) if path else None

        # Get current directory name for display
        current_dir = os.path.basename(full_path) or os.path.basename(share_path) or 'Root'

        return render_template(
            'directory.html',
            current_dir=current_dir,
            parent_path=parent_path,
            folders=folders,
            files=files,
            path=path
        )

    # If neither file nor directory, return 404
    abort(404)

def run_flask_server(path, host_ip, user_port=None):
    global share_path, is_file_share
    share_path = os.path.abspath(path)
    is_file_share = os.path.isfile(share_path)

    # Use user-specified port or auto-detect
    port = user_port or PORT
    if not user_port:
        # Find an available port starting at PORT
        for test_port in range(PORT, PORT + 10):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex((DEFAULT_INTERFACE, test_port)) != 0:
                    port = test_port
                    break
    else:
        # Check if specified port is available
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex((DEFAULT_INTERFACE, port)) == 0:
                print(f"Error: Port {port} is already in use.")
                sys.exit(1)

    url = f"http://{host_ip}:{port}"
    if is_file_share:
        file_name = os.path.basename(share_path)
        url += f"/{urllib.parse.quote(file_name)}"

    print(f"\nQuickShare server started at {url}")
    print(f"Bound to all interfaces but QR shows {host_ip}")
    print("Press Ctrl+C to stop the server.\n")
    generate_qr_code(url)

    app.run(host=DEFAULT_INTERFACE, port=port, debug=False)

def main():
    parser = argparse.ArgumentParser(description="QuickShare - Simple file sharing via Flask and QR")
    parser.add_argument(
        "path",
        help="Path to file or directory to share"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to use (default: auto-select from 8000)"
    )

    args = parser.parse_args()

    share_path = os.path.abspath(args.path)
    if not os.path.exists(share_path):
        print(f"Error: Path '{share_path}' does not exist.")
        sys.exit(1)

    host_ip = select_interface()
    if not host_ip:
        print("No valid network interface found. Exiting.")
        sys.exit(1)

    run_flask_server(share_path, host_ip, args.port)

if __name__ == "__main__":
    main()
