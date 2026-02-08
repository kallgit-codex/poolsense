"""
MS5837-30BA Pressure Sensor Driver for Raspberry Pi
I2C interface — reads pressure + temperature
Resolution: 0.2 mbar (~2mm water depth)
"""

import smbus2
import time

class MS5837:
    """Driver for MS5837-30BA waterproof pressure sensor."""
    
    ADDR = 0x76
    
    # Commands
    CMD_RESET = 0x1E
    CMD_PROM_READ = 0xA0  # Base address, add 0x00-0x0E for registers
    CMD_ADC_READ = 0x00
    CMD_CONVERT_D1_8192 = 0x4A  # Pressure, OSR=8192 (highest precision)
    CMD_CONVERT_D2_8192 = 0x5A  # Temperature, OSR=8192
    
    # Fluid densities (kg/m³)
    DENSITY_FRESHWATER = 997.0
    DENSITY_SALTWATER = 1029.0
    
    def __init__(self, bus_num=1):
        self.bus = smbus2.SMBus(bus_num)
        self.C = [0] * 8  # Calibration coefficients
        self.D1 = 0  # Raw pressure
        self.D2 = 0  # Raw temperature
        self.pressure_mbar = 0.0
        self.temperature_c = 0.0
        self.fluid_density = self.DENSITY_FRESHWATER
        self._initialize()
    
    def _initialize(self):
        """Reset sensor and read calibration PROM."""
        # Reset
        self.bus.write_byte(self.ADDR, self.CMD_RESET)
        time.sleep(0.01)
        
        # Read 7 calibration values from PROM
        for i in range(7):
            data = self.bus.read_i2c_block_data(self.ADDR, self.CMD_PROM_READ + (i * 2), 2)
            self.C[i] = (data[0] << 8) | data[1]
        
        # Verify CRC (simplified — production code should validate)
        print(f"[MS5837] Initialized. Calibration: {self.C[1:7]}")
    
    def read(self):
        """Take a pressure + temperature reading. Returns True on success."""
        try:
            # Request D1 (pressure) conversion
            self.bus.write_byte(self.ADDR, self.CMD_CONVERT_D1_8192)
            time.sleep(0.02)  # Wait for conversion at OSR 8192
            
            # Read D1
            data = self.bus.read_i2c_block_data(self.ADDR, self.CMD_ADC_READ, 3)
            self.D1 = (data[0] << 16) | (data[1] << 8) | data[2]
            
            # Request D2 (temperature) conversion
            self.bus.write_byte(self.ADDR, self.CMD_CONVERT_D2_8192)
            time.sleep(0.02)
            
            # Read D2
            data = self.bus.read_i2c_block_data(self.ADDR, self.CMD_ADC_READ, 3)
            self.D2 = (data[0] << 16) | (data[1] << 8) | data[2]
            
            # Calculate compensated values (from MS5837-30BA datasheet)
            self._calculate()
            return True
            
        except Exception as e:
            print(f"[MS5837] Read error: {e}")
            return False
    
    def _calculate(self):
        """Apply calibration coefficients per MS5837-30BA datasheet."""
        C = self.C
        
        # First order compensation
        dT = self.D2 - C[5] * 256
        SENS = C[1] * 65536 + (C[3] * dT) // 128
        OFF = C[2] * 131072 + (C[4] * dT) // 64
        
        self.temperature_c = (2000 + (dT * C[6]) // 8388608) / 100.0
        
        # Second order compensation
        T2 = 0
        OFF2 = 0
        SENS2 = 0
        
        Ti = (2000 + (dT * C[6]) // 8388608)
        
        if Ti < 2000:  # Low temp compensation
            T2 = 11 * (dT * dT) // 34359738368
            OFF2 = 31 * (Ti - 2000) ** 2 // 8
            SENS2 = 63 * (Ti - 2000) ** 2 // 32
        
        OFF -= OFF2
        SENS -= SENS2
        
        self.pressure_mbar = ((self.D1 * SENS // 2097152) - OFF) / 32768.0 / 100.0
        self.temperature_c -= T2 / 100.0
    
    def depth_mm(self):
        """Convert pressure to water depth in mm."""
        # Subtract atmospheric pressure (~1013.25 mbar at sea level)
        # In practice, we calibrate against the first reading
        water_pressure_pa = (self.pressure_mbar - 1013.25) * 100.0
        depth_m = water_pressure_pa / (self.fluid_density * 9.80665)
        return depth_m * 1000.0  # Convert to mm
    
    def pressure(self):
        """Return pressure in mbar."""
        return self.pressure_mbar
    
    def temperature(self):
        """Return temperature in °C."""
        return self.temperature_c


if __name__ == "__main__":
    sensor = MS5837()
    
    print("Reading MS5837-30BA...")
    print(f"{'Time':>8}  {'Pressure (mbar)':>16}  {'Temp (°C)':>10}  {'Depth (mm)':>11}")
    print("-" * 55)
    
    while True:
        if sensor.read():
            print(f"{'':>8}  {sensor.pressure():>16.2f}  {sensor.temperature():>10.2f}  {sensor.depth_mm():>11.2f}")
        time.sleep(1)
