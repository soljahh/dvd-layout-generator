#!/usr/bin/env python3
"""
GTA San Andreas (Xbox 360) — DVDFileLayout.txt Generator
Scans a game directory and generates a DVDFileLayout.txt matching
the original disc layout format.

Can operate in two modes:
  1. Template mode: reads an existing DVDFileLayout.txt, updates file sizes
  2. Scan mode: scans a game folder and generates from scratch

Usage: Run with no arguments for GUI, or pass a game directory path for CLI.
"""

import struct
import os
import sys
import math

# ── Constants ───────────────────────────────────────────────────────────────

BLOCK_SIZE = 2048
MAX_LBA = 1783936  # Total sectors on the game disc
SEEK_TIME_MS = 2.2  # Base seek time for single-block reads
INNER_RATE = 6.5    # MB/s at inner edge
OUTER_RATE = 13.2   # MB/s at outer edge

# ── DVD Layout Calculations ────────────────────────────────────────────────

def calc_blocks(file_size):
    """Calculate number of 2048-byte blocks for a file."""
    if file_size <= 0:
        return 1  # directories/markers are 1 block
    return max(1, math.ceil(file_size / BLOCK_SIZE))


def calc_read_rate(lba):
    """Calculate DVD read rate at a given LBA position.
    Uses power-law fit matching original Xbox 360 DVD speed profile."""
    position = max(0, min(1, lba / MAX_LBA))
    rate = INNER_RATE + (OUTER_RATE - INNER_RATE) * (position ** 0.8)
    return round(rate * 10) / 10  # round to 0.1


def calc_read_time(blocks, rate_start, rate_end=None):
    """Calculate read time in ms for a file."""
    if blocks <= 1:
        # Single block = seek time only (varies slightly by position)
        return round(SEEK_TIME_MS + rate_start * 0.01, 1)

    # For multi-block files, use average rate
    if rate_end is None:
        rate_end = rate_start
    avg_rate = (rate_start + rate_end) / 2
    data_mb = blocks * BLOCK_SIZE / 1e6
    time_ms = data_mb / avg_rate * 1000
    return round(time_ms, 1)


def format_time(time_ms):
    """Format time as '1234.5 ms' or '12.3 s'."""
    if time_ms >= 1000:
        return f"{time_ms / 1000:.1f} s"
    return f"{time_ms:.1f} ms"


def format_rate(rate_start, rate_end):
    """Format rate as '12.3 MB/s' or '12.0 - 12.3 MB/s'."""
    if abs(rate_start - rate_end) < 0.15:
        return f"{max(rate_start, rate_end):.1f} MB/s"  # Use the higher rate
    lo = min(rate_start, rate_end)
    hi = max(rate_start, rate_end)
    return f"{lo:.1f} - {hi:.1f} MB/s"


# ── Template Parser ─────────────────────────────────────────────────────────

def parse_template(template_path):
    """Parse an existing DVDFileLayout.txt into a list of entries."""
    entries = []
    with open(template_path, 'r') as f:
        lines = f.readlines()

    for line in lines[1:]:  # skip header
        parts = line.strip().split('\t')
        if len(parts) >= 4:
            entries.append({
                'ordinal': int(parts[0]),
                'name': parts[1],
                'lba': int(parts[2]),
                'blocks': int(parts[3]),
                'is_dir': int(parts[3]) == 1 and '.' not in parts[1] and parts[1] not in ['\\'],
            })
    return entries


# ── Layout Generator ────────────────────────────────────────────────────────

def generate_layout(game_dir, template_entries=None):
    """Generate DVDFileLayout.txt content.
    
    If template_entries is provided, uses that file order and updates sizes.
    Otherwise, generates from scanning the directory.
    """
    output_lines = []

    if template_entries:
        # Template mode: update blocks based on actual file sizes
        entries = []
        for te in template_entries:
            name = te['name']
            blocks = te['blocks']

            # Try to find the file in game_dir
            file_path = os.path.join(game_dir, name)
            if os.path.isfile(file_path):
                file_size = os.path.getsize(file_path)
                blocks = calc_blocks(file_size)
            elif os.path.isdir(file_path) or te['is_dir']:
                blocks = 1
            # else: keep original blocks (file not found in directory)

            entries.append({
                'ordinal': te['ordinal'],
                'name': name,
                'blocks': blocks,
            })
    else:
        # Scan mode: walk the directory
        all_files = []
        for root, dirs, files in os.walk(game_dir):
            rel_root = os.path.relpath(root, game_dir)
            if rel_root != '.':
                all_files.append((rel_root, 0, True))
            for f in sorted(files):
                fpath = os.path.join(root, f)
                fsize = os.path.getsize(fpath)
                rel_path = os.path.relpath(fpath, game_dir)
                all_files.append((rel_path, fsize, False))

        entries = []
        for i, (name, size, is_dir) in enumerate(all_files):
            blocks = 1 if is_dir else calc_blocks(size)
            entries.append({'ordinal': i, 'name': name, 'blocks': blocks})

        # Add system entries at the end
        entries.append({'ordinal': len(entries), 'name': 'Reserved', 'blocks': 4096})
        entries.append({'ordinal': len(entries), 'name': 'Volume Descriptor', 'blocks': 1})

    # Calculate LBAs (descending from last sector)
    # Pattern: LBA[0] = MAX_LBA - 1, LBA[i] = LBA[i-1] - blocks[i]
    current_lba = MAX_LBA - 1
    for i, entry in enumerate(entries):
        # Special handling for system entries
        if entry['name'] == 'Reserved':
            entry['lba'] = 48
            continue
        elif entry['name'] == 'Volume Descriptor':
            entry['lba'] = 32
            continue
        elif entry['name'] == '\\':
            current_lba -= entry['blocks']
            entry['lba'] = current_lba
            continue
        elif entry['name'] == '$SystemUpdate':
            current_lba -= entry['blocks']
            entry['lba'] = current_lba
            continue

        if i == 0:
            entry['lba'] = current_lba
        else:
            current_lba -= entry['blocks']
            entry['lba'] = current_lba

    # Generate output
    header = "Ordinal\tName\tLBA\tBlocks\tRead Time\tRead Rate"
    output_lines.append(header)

    stats = {'files': 0, 'updated': 0, 'total_blocks': 0}

    for entry in entries:
        lba = entry['lba']
        blocks = entry['blocks']
        stats['files'] += 1
        stats['total_blocks'] += blocks

        # Calculate rates at start and end of file
        rate_start = calc_read_rate(lba)
        rate_end = calc_read_rate(max(0, lba - blocks))

        # Calculate read time
        time_ms = calc_read_time(blocks, rate_start, rate_end)

        line = (f"{entry['ordinal']}\t{entry['name']}\t{lba}\t{blocks}\t"
                f"{format_time(time_ms)}\t{format_rate(rate_start, rate_end)}")
        output_lines.append(line)

    return '\n'.join(output_lines) + '\n', stats


# ── GUI ─────────────────────────────────────────────────────────────────────

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title("GTA SA Xbox 360 — DVDFileLayout Generator")
    root.configure(bg='#1a1a2e')
    root.geometry("640x560")
    root.minsize(520, 440)

    BG = '#1a1a2e'
    BG2 = '#16213e'
    FG = '#e0e0e0'
    ACCENT = '#ff6b35'
    GREEN = '#35ff6b'
    MONO = ('Consolas', 9) if sys.platform == 'win32' else ('Courier', 10)
    FONT = ('Segoe UI', 10) if sys.platform == 'win32' else ('Helvetica', 10)
    FONT_BOLD = ('Segoe UI', 10, 'bold') if sys.platform == 'win32' else ('Helvetica', 10, 'bold')
    FONT_TITLE = ('Segoe UI', 14, 'bold') if sys.platform == 'win32' else ('Helvetica', 14, 'bold')

    # ── Title ──
    title_frame = tk.Frame(root, bg=BG, pady=12)
    title_frame.pack(fill='x')
    tk.Label(title_frame, text="DVDFileLayout Generator", font=FONT_TITLE,
             fg=ACCENT, bg=BG).pack()
    tk.Label(title_frame, text="GTA San Andreas · Xbox 360 · Regenerate disc layout after modding",
             font=('Segoe UI', 8) if sys.platform == 'win32' else ('Helvetica', 8),
             fg='#666', bg=BG).pack()

    # ── Input section ──
    input_frame = tk.Frame(root, bg=BG, padx=20, pady=4)
    input_frame.pack(fill='x')

    # Template file
    tk.Label(input_frame, text="Template DVDFileLayout.txt (optional):",
             font=FONT, fg='#888', bg=BG, anchor='w').pack(fill='x')

    template_row = tk.Frame(input_frame, bg=BG)
    template_row.pack(fill='x', pady=(2, 8))

    template_var = tk.StringVar()
    template_entry = tk.Entry(template_row, textvariable=template_var, font=MONO,
                               bg='#0d1117', fg=FG, insertbackground=FG,
                               relief='flat', highlightthickness=1,
                               highlightbackground='#333', highlightcolor=ACCENT)
    template_entry.pack(side='left', fill='x', expand=True, padx=(0, 6))

    def browse_template():
        path = filedialog.askopenfilename(
            title="Select DVDFileLayout.txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            template_var.set(path)

    tk.Button(template_row, text="Browse", command=browse_template, font=FONT,
              bg=BG2, fg=FG, relief='flat', padx=8, cursor='hand2').pack(side='right')

    # Game directory
    tk.Label(input_frame, text="Game directory (folder with game files):",
             font=FONT, fg='#888', bg=BG, anchor='w').pack(fill='x')

    dir_row = tk.Frame(input_frame, bg=BG)
    dir_row.pack(fill='x', pady=(2, 8))

    dir_var = tk.StringVar()
    dir_entry = tk.Entry(dir_row, textvariable=dir_var, font=MONO,
                          bg='#0d1117', fg=FG, insertbackground=FG,
                          relief='flat', highlightthickness=1,
                          highlightbackground='#333', highlightcolor=ACCENT)
    dir_entry.pack(side='left', fill='x', expand=True, padx=(0, 6))

    def browse_dir():
        path = filedialog.askdirectory(title="Select game directory")
        if path:
            dir_var.set(path)

    tk.Button(dir_row, text="Browse", command=browse_dir, font=FONT,
              bg=BG2, fg=FG, relief='flat', padx=8, cursor='hand2').pack(side='right')

    # ── Generate button ──
    btn_frame = tk.Frame(root, bg=BG, pady=4, padx=20)
    btn_frame.pack(fill='x')

    def do_generate():
        game_dir = dir_var.get().strip()
        template_path = template_var.get().strip()

        if not game_dir or not os.path.isdir(game_dir):
            log_text.insert('end', "Error: Please select a valid game directory.\n\n", 'error')
            return

        template_entries = None
        if template_path and os.path.isfile(template_path):
            try:
                template_entries = parse_template(template_path)
                log_text.insert('end', f"Using template: {os.path.basename(template_path)} "
                                       f"({len(template_entries)} entries)\n", 'info')
            except Exception as e:
                log_text.insert('end', f"Template error: {e}\nFalling back to scan mode.\n", 'warn')

        try:
            content, stats = generate_layout(game_dir, template_entries)

            # Save to game directory
            out_path = os.path.join(game_dir, 'DVDFileLayout.txt')
            with open(out_path, 'w') as f:
                f.write(content)

            log_text.insert('end', f"\n{'─' * 50}\n", 'dim')
            log_text.insert('end', f"✓ DVDFileLayout.txt generated successfully!\n", 'success')
            log_text.insert('end', f"  Saved to: {out_path}\n", 'dim')
            log_text.insert('end', f"  Files: {stats['files']}\n", 'dim')
            log_text.insert('end', f"  Total blocks: {stats['total_blocks']:,}\n\n", 'dim')

        except Exception as e:
            log_text.insert('end', f"\nError: {e}\n\n", 'error')

        log_text.see('end')

    gen_btn = tk.Button(btn_frame, text="⚡  Generate DVDFileLayout.txt",
                        command=do_generate, font=FONT_BOLD,
                        bg=ACCENT, fg='white', activebackground='#e55a28',
                        activeforeground='white', relief='flat',
                        padx=16, pady=6, cursor='hand2')
    gen_btn.pack(fill='x')

    # ── Log area ──
    log_frame = tk.Frame(root, bg=BG, padx=20, pady=8)
    log_frame.pack(fill='both', expand=True)

    log_text = tk.Text(log_frame, bg='#0d1117', fg=FG, font=MONO,
                       relief='flat', padx=12, pady=10, wrap='word',
                       insertbackground=FG, selectbackground='#264f78',
                       borderwidth=0, highlightthickness=1,
                       highlightbackground='#333', highlightcolor=ACCENT)
    log_text.pack(fill='both', expand=True)

    scrollbar = tk.Scrollbar(log_text, command=log_text.yview)
    scrollbar.pack(side='right', fill='y')
    log_text.configure(yscrollcommand=scrollbar.set)

    log_text.tag_configure('header', foreground=ACCENT, font=FONT_BOLD)
    log_text.tag_configure('success', foreground=GREEN)
    log_text.tag_configure('error', foreground='#ff4444')
    log_text.tag_configure('warn', foreground='#ffb835')
    log_text.tag_configure('dim', foreground='#666')
    log_text.tag_configure('info', foreground='#88aacc')

    log_text.insert('end', "DVDFileLayout.txt Generator\n\n", 'header')
    log_text.insert('end', "Two modes:\n", 'dim')
    log_text.insert('end', "  1. With template: provide the original DVDFileLayout.txt\n"
                           "     + your game folder. Preserves file order, updates sizes.\n\n", 'dim')
    log_text.insert('end', "  2. Without template: just select the game folder.\n"
                           "     Scans all files and generates a new layout.\n\n", 'dim')

    root.mainloop()


# ── CLI ─────────────────────────────────────────────────────────────────────

def run_cli(game_dir, template_path=None):
    print("GTA SA Xbox 360 — DVDFileLayout Generator")
    print("=" * 44)

    template_entries = None
    if template_path and os.path.isfile(template_path):
        template_entries = parse_template(template_path)
        print(f"Template: {template_path} ({len(template_entries)} entries)")

    content, stats = generate_layout(game_dir, template_entries)

    out_path = os.path.join(game_dir, 'DVDFileLayout.txt')
    with open(out_path, 'w') as f:
        f.write(content)

    print(f"Generated: {out_path}")
    print(f"Files: {stats['files']}, Total blocks: {stats['total_blocks']:,}")


# ── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) > 1:
        game_dir = sys.argv[1]
        template = sys.argv[2] if len(sys.argv) > 2 else None
        run_cli(game_dir, template)
    else:
        try:
            run_gui()
        except ImportError:
            print("tkinter not available. CLI usage:")
            print(f"  python {sys.argv[0]} <game_directory> [template.txt]")
            sys.exit(1)
