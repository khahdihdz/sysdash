# sysdash

Bảng điều khiển hệ thống dạng TUI cho **Termux/Android**: CPU, RAM, pin, nhiệt độ, mạng, lưu trữ, tiến trình và Termux API. Không cần root, không mở cổng mạng, không gửi dữ liệu ra ngoài.

## Cài bằng một lệnh

```bash
curl -fsSL https://raw.githubusercontent.com/khahdihdz/sysdash/main/install.sh | bash
```

Lệnh trên tải source, cài vào `~/.local/share/sysdash`, tạo lệnh `$PREFIX/bin/sysdash`, tạo config mặc định và shortcut **Termux:Widget** (nếu app đã cài).

Muốn xem script trước khi chạy:

```bash
curl -fsSL https://raw.githubusercontent.com/khahdihdz/sysdash/main/install.sh -o /tmp/sysdash-install.sh
less /tmp/sysdash-install.sh
bash /tmp/sysdash-install.sh
```

Tùy chọn khi cài:

```bash
SYSDASH_TASKS=1 curl -fsSL .../install.sh | bash   # thêm task snapshot
SYSDASH_NO_WIDGET=1 curl -fsSL .../install.sh | bash
SYSDASH_FORCE=1 curl -fsSL .../install.sh | bash    # ghi đè wrapper lạ
```

## Dùng

```bash
sysdash                 # menu chính
sysdash dashboard       # dashboard tương tác
sysdash once            # in snapshot một lần
sysdash json            # JSON cho script
sysdash snapshot        # lưu JSON + rotate
sysdash settings        # menu cài đặt
sysdash doctor          # kiểm tra môi trường
sysdash install         # cài/sửa lệnh
sysdash uninstall       # gỡ cài đặt
```

Phím trong dashboard:

| Phím | Chức năng |
|---|---|
| `q` / `Ctrl+C` | thoát dashboard (về menu nếu mở từ menu) |
| `m` | mở menu |
| `,` | mở cài đặt |
| `p` | tạm dừng cập nhật |
| `r` | làm mới ngay |
| `+` / `-` | tăng/giảm interval |
| `s` | đổi sắp xếp tiến trình CPU/RAM |
| `h` / `?` | trợ giúp |

Menu dùng `↑/↓` hoặc `j/k`, `Enter` để chọn, `Esc` quay lại, `1–9` chọn nhanh, `q` thoát.

## Termux:Widget

Cài app **Termux:Widget** từ F-Droid (cùng nguồn với Termux), rồi:

```bash
sysdash widget install            # shortcut mở dashboard
sysdash widget install --tasks    # thêm task snapshot chạy nền
sysdash widget list
sysdash widget doctor
sysdash widget remove
```

Trên màn hình chính Android: giữ chỗ trống → **Widgets** → **Termux:Widget** → chọn **System Dashboard**. Với task, dùng widget **Termux:Widget (Tasks)** → **Sysdash Snapshot**.

- `~/.shortcuts/System Dashboard`: mở terminal và chạy dashboard.
- `~/.shortcuts/tasks/Sysdash Snapshot`: chạy nền, lưu JSON vào `~/.local/state/sysdash/snapshots`, giữ 20 file gần nhất.
- Không thể tự thêm widget lên launcher bằng CLI; sau khi tạo script, thêm widget thủ công.
- Không có lệnh `termux-widget`; đây là app Android riêng.

## Gỡ

```bash
sysdash uninstall              # giữ config
sysdash uninstall --purge      # xóa config, log, snapshot
sysdash uninstall --purge --purge-source
```

Hoặc một dòng:

```bash
curl -fsSL https://raw.githubusercontent.com/khahdihdz/sysdash/main/uninstall.sh | bash -s -- --yes
```

Sau khi gỡ, nhớ xóa widget còn trên launcher (giữ widget → **Remove**).

## Yêu cầu

- Termux trên Android (đã kiểm tra Android 15, aarch64).
- Python >= 3.10 (khuyến nghị bản Termux mới).
- `rich` để hiển thị đẹp; nếu thiếu, dùng fallback ANSI.
- `termux-api` + app Termux:API để đọc pin/Wi-Fi/thông báo.
- `psutil` là tùy chọn; code đọc `/proc` trực tiếp nên không bắt buộc.

## Cấu hình và dữ liệu

- Config: `~/.config/sysdash/config.toml`
- Log: `~/.local/state/sysdash/sysdash.log`
- Snapshot: `~/.local/state/sysdash/snapshots/`
- Source cài đặt: `~/.local/share/sysdash/`
- Wrapper: `$PREFIX/bin/sysdash`
- Shortcut: `~/.shortcuts/` và `~/.shortcuts/tasks/`

Config có thể sửa tay:

```toml
interval = 1.0
top_n = 5
wake_lock = false
color = true
lang = "vi"
notify = false
snapshot_keep = 20

[thresholds]
cpu_warn = 60.0
cpu_crit = 85.0
ram_warn = 60.0
ram_crit = 85.0
battery_warn = 30.0
battery_crit = 15.0
temp_warn = 45.0
temp_crit = 60.0

[collectors]
cpu = true
memory = true
battery = true
thermal = true
network = true
storage = true
processes = true
termux = true
```

`battery_warn`/`battery_crit` là ngưỡng **thấp**: pin dưới mức này sẽ vàng/đỏ.

## Phát triển

```bash
python -m pytest -q
python -m sysdash once
python -m sysdash json | python -m json.tool
SYSDASH_DEBUG=1 python -m sysdash doctor
```

Cấu trúc chính:

```text
sysdash/
  cli.py          # argparse, dispatch
  app.py          # vòng lặp dashboard, snapshot
  collectors.py   # CPU, RAM, pin, nhiệt độ, mạng, storage, process, Termux
  ui.py           # render rich/ANSI, menu, phím
  menu.py         # menu chính, settings, widget
  installer.py    # install / repair / doctor / uninstall
  widget.py       # Termux:Widget
  config.py       # config.toml
```

## Bảo mật

- Không dùng `sudo`, không sửa `/system`.
- Chỉ ghi trong `$PREFIX/bin`, `~/.local`, `~/.config`, `~/.cache`, `~/.shortcuts`.
- Không gửi dữ liệu ra ngoài; không mở cổng mạng.
- Installer từ chối ghi đè file không có marker của sysdash, trừ khi có `--force`.
- `--dry-run` xem trước mọi thay đổi của install/uninstall/widget.

## Giấy phép

MIT.
