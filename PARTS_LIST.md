# PoolSense — DIY Leak Detection System
## Parts List & Build Guide

---

## AMAZON SHOPPING LIST

### Core Components

| # | Component | Price | Link |
|---|-----------|-------|------|
| 1 | **Raspberry Pi Zero 2 W** | ~$15 | [Amazon](https://www.amazon.com/Raspberry-Zero-Bluetooth-RPi-2W/dp/B09LH5SBPS) |
| 2 | **MS5837-30BA Pressure Sensor** (waterproof, I2C, 0.2mbar resolution = 2mm water depth) | ~$15-25 | [Amazon](https://www.amazon.com/RAKSTORE-GY-MS5837-Precision-Waterproof-Pressure/dp/B0BC11Q4SP) |
| 3 | **DS18B20 Waterproof Temp Probe** (3m cable) | ~$6 | Search: "DS18B20 waterproof temperature sensor" |
| 4 | **32GB MicroSD Card** (Class 10) | ~$7 | Any brand works |
| 5 | **Micro USB Power Supply 5V 2.5A** | ~$8 | Search: "Raspberry Pi Zero power supply" |

### Wiring & Connectors

| # | Component | Price | Link |
|---|-----------|-------|------|
| 6 | **GPIO Header Pins (2x20)** — solder to Pi Zero | ~$2 | Often included in Pi Zero kits |
| 7 | **Dupont Jumper Wires (F-F)** | ~$5 | Search: "dupont jumper wire female" |
| 8 | **4.7kΩ Resistor (pack)** — pull-up for I2C + DS18B20 | ~$3 | Any pack |
| 9 | **Small Breadboard** (optional, for prototyping) | ~$3 | Any mini breadboard |

### Enclosure & Waterproofing

| # | Component | Price | Link |
|---|-----------|-------|------|
| 10 | **IP65 Waterproof Project Box** (~150x100x70mm) | ~$8 | Search: "waterproof junction box IP65" |
| 11 | **Cable Glands (PG7, pack of 10)** — seal cables through box | ~$5 | Search: "PG7 cable gland waterproof" |
| 12 | **Silicone Sealant / Marine Epoxy** | ~$5 | For sensor cable entry sealing |

### Nice-to-Have (Phase 2)

| # | Component | Price | Link |
|---|-----------|-------|------|
| 13 | **0.96" OLED Display (SSD1306, I2C)** — poolside readout | ~$6 | Search: "SSD1306 OLED I2C" |
| 14 | **LiPo Battery 3.7V 5000mAh** | ~$12 | Makes it portable |
| 15 | **TP4056 USB Charge Board** | ~$3 | Charge the LiPo via USB |
| 16 | **5V Boost Converter** | ~$3 | Step up 3.7V LiPo to 5V for Pi |
| 17 | **BME280 Sensor** — temp + humidity + barometric for evap modeling | ~$5 | Search: "BME280 I2C module" |
| 18 | **Momentary Push Button** — start/stop test | ~$2 | Any panel-mount button |

---

## COST SUMMARY

| Build | Total |
|-------|-------|
| **Minimum Viable (just works)** | **~$65** |
| **Full Featured (display + battery)** | **~$95** |
| **Commercial Leakalyzer** | **$2,000** |

---

## WIRING DIAGRAM

```
Raspberry Pi Zero 2 W GPIO Pinout:

                    +-----+
               3V3  | 1  2| 5V
  MS5837 SDA → GP2  | 3  4| 5V
  MS5837 SCL → GP3  | 5  6| GND ← MS5837 GND
         DS18B20 → GP4  | 7  8| GP14
               GND  | 9 10| GP15
                    |11 12| GP18
                    |13 14| GND
                    |15 16| GP23
               3V3  |17 18| GP24
                    |19 20| GND
                    |21 22| GP25
                    |23 24| GP8
                    |25 26| GP7
                    +-----+

MS5837-30BA (I2C):
  VDD  → Pin 1 (3.3V)
  GND  → Pin 6 (GND)
  SDA  → Pin 3 (GPIO2) + 4.7kΩ pull-up to 3.3V
  SCL  → Pin 5 (GPIO3) + 4.7kΩ pull-up to 3.3V

DS18B20 (1-Wire):
  VCC (Red)    → Pin 1 (3.3V)
  GND (Black)  → Pin 9 (GND)
  Data (Yellow) → Pin 7 (GPIO4) + 4.7kΩ pull-up to 3.3V

OLED SSD1306 (I2C, same bus as MS5837):
  VCC  → Pin 1 (3.3V)
  GND  → Pin 6 (GND)
  SDA  → Pin 3 (GPIO2)
  SCL  → Pin 5 (GPIO3)
  (Different I2C address, no conflict — MS5837=0x76, SSD1306=0x3C)
```

---

## PI SETUP (first boot)

```bash
# 1. Flash Raspberry Pi OS Lite (no desktop needed) to SD card
#    Use Raspberry Pi Imager — set WiFi credentials + enable SSH during flashing

# 2. SSH into Pi
ssh pi@poolsense.local

# 3. Enable I2C and 1-Wire
sudo raspi-config
# → Interface Options → I2C → Enable
# → Interface Options → 1-Wire → Enable
# Reboot

# 4. Install dependencies
sudo apt update && sudo apt install -y python3-pip python3-smbus i2c-tools
pip3 install flask smbus2 w1thermsensor requests --break-system-packages

# 5. Verify sensor detection
i2cdetect -y 1
# Should show device at 0x76 (MS5837)
```

---

## HOW IT WORKS (THE ALGORITHM)

1. **Baseline Phase (3 min)**: Pump off. Record pressure readings every 1s. Let sensor settle and establish stable water level baseline.
2. **Measurement Phase (20 min)**: Continue recording pressure. Temperature sensor tracks water temp for evaporation compensation. 1,200+ data points captured.
3. **Evaporation Model**: Using water temp + ambient conditions (optional weather API), calculate expected evaporation rate.
4. **Leak Calculation**: `actual_loss - expected_evaporation = leak_rate`
5. **Report**: Generate results — leak rate in gallons/hour, bucket test equivalent, pass/fail determination.

**Why 20 minutes works**: At 1 reading/sec with 0.2mbar (2mm) resolution, even a small leak produces a statistically significant downward trend across 1,200+ samples. The commercial Leakalyzer uses the same timeframe.

### Key Thresholds
- **< 0.5mm/hr water loss after evap correction** → No significant leak
- **0.5-2mm/hr** → Possible slow leak, recommend further investigation
- **> 2mm/hr** → Confirmed leak, measure rate for repair scoping

---

## PRODUCT ROADMAP

### v1.0 — Prove It Works (Week 1-2)
- Basic pressure + temp reading
- Web dashboard on local network
- Manual start/stop
- Test on real pools at work

### v1.5 — Field Ready (Week 3-4)
- Battery powered + portable case
- OLED display for poolside readings
- Auto-generate PDF reports
- Weather API integration for evap modeling

### v2.0 — Product (Month 2-3)
- Custom PCB (eliminate breadboard)
- 3D printed case
- Mobile app (React Native)
- Cloud dashboard + customer report portal
- Branding: "PoolSense" or similar

### v3.0 — SaaS (Month 4+)
- Subscription model ($10-15/mo)
- Fleet management (multiple units)
- Historical data + analytics
- API for integration with pool service software
- White-label option for pool companies
