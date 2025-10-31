# 🎛️ Admin UI Migration Notice

## ⚠️ Important Change

The **Admin Panel UI** has been migrated to a separate project for better maintainability and scalability.

### Previous Location (Deprecated)
- ❌ `admin_api.py` - Removed
- ❌ `static/index.html` - Removed
- ❌ `start_admin_panel.sh` - Removed

### New Location
The Admin UI is now in: **`crypto-trader-ui`** (separate project)

```
/home/ubuntu/crypto-trader-ui/
├── main.py                    # Admin API (formerly admin_api.py)
├── static/index.html          # Dashboard
├── start_ui.sh               # Start script
└── README.md                  # Full documentation
```

---

## 🚀 Quick Start

### Access the Admin UI

```bash
cd /home/ubuntu/crypto-trader-ui
./start_ui.sh
```

**Dashboard**: http://localhost:8080

---

## 📖 Why the Separation?

### crypto-listener-rest (This Project)
**Purpose**: Trade execution REST API only
- ✅ Execute trades via `/execute-trade`
- ✅ Guardian integration via `/guardian`
- ✅ Health checks and stats
- ✅ Focus on trading logic

### crypto-trader-ui (Separate Project)
**Purpose**: Admin control panel
- ✅ Pause/resume users
- ✅ Configure tier filtering
- ✅ Circuit breaker settings
- ✅ Emergency controls
- ✅ Logs viewer
- ✅ Extensible for new admin features

---

## 🔗 Shared Resources

Both projects share:
- 🗄️ **PostgreSQL Database** - Same `user_rules` table
- 🔴 **Redis** - Same keys for positions
- ⚙️ **Configuration** - Same `.env` variables

**No HTTP dependencies** - Services operate independently

---

## 📦 Migration Benefits

### Before (Coupled)
```
crypto-listener-rest/
├── main.py          (Trading API)
├── admin_api.py     (Admin UI)
└── static/          (Dashboard)
```
❌ Tightly coupled
❌ Hard to extend UI
❌ Single deployment

### After (Separated)
```
crypto-listener-rest/      crypto-trader-ui/
├── main.py (Trading)     ├── main.py (Admin)
                          ├── static/ (Dashboard)
                          └── app/features/ (Extensible)
```
✅ Independent deployments
✅ Easier to maintain
✅ Scalable architecture
✅ Clean separation of concerns

---

## 🛠️ For Developers

### Running Both Services

**Terminal 1 - Trading API:**
```bash
cd /home/ubuntu/crypto-listener-rest
nohup uvicorn main:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1 &
```

**Terminal 2 - Admin UI:**
```bash
cd /home/ubuntu/crypto-trader-ui
./start_ui.sh
```

### Systemd Services

```bash
# Trading API
sudo systemctl start crypto-listener

# Admin UI
sudo systemctl start crypto-ui
```

---

## 📚 Documentation

- **Trading API Docs**: http://localhost:8000/docs
- **Admin UI Docs**: http://localhost:8080/docs
- **Admin UI README**: `/home/ubuntu/crypto-trader-ui/README.md`

---

## 🔒 Security Note

- **crypto-listener-rest**: Internal only (127.0.0.1:8000)
- **crypto-trader-ui**: Public with auth (0.0.0.0:8080 via Cloudflare Tunnel)

---

## 📝 Migration Date

**Migrated**: January 2025
**Reason**: Separate UI logic from trading logic for better maintainability

---

For questions or issues, refer to `crypto-trader-ui/README.md`
