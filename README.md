# OAC – Control de Nivel de Líquidos

Escena de demostración de **Open Automation Components (OAC)**: un lazo de control de nivel donde el **PLC Siemens** decide la apertura de las válvulas con un bloque `PID_Level`, y **CoppeliaSim** simula cómo responde el tanque.

Igual que en una planta real, el PLC solo **comanda actuadores** (válvulas `V_In` / `V_Out`) y **lee sensores** (nivel `L_Level`). La física del proceso vive en el simulador: el caudal de salida depende de la apertura de la válvula y también del nivel del tanque.

```
 TIA Portal / PLCSIM Advanced  ⇄  OPC UA  ⇄  OAC_PLC_Bridge.py (add-on)  ⇄  CoppeliaSim
   PID_Level, DB_Tank                                                       tanque, válvulas, HMI
```

---

## Contenido del repositorio

| Carpeta / archivo | Qué es |
|---|---|
| `scene/TankLevel.ttt` | Escena de CoppeliaSim (tanque, válvulas, panel Start/Stop y HMI). Los scripts van embebidos en ella. |
| `bridge/OAC_PLC_Bridge.py` | Puente OPC UA ↔ CoppeliaSim. Se instala como **add-on** y arranca solo al dar Play. |
| `plc/Project8.zap19` | Proyecto de TIA Portal V19 archivado (`DB_Tank`, `PID_Level`, enclavamiento Start/Stop). |
| `requirements.txt` | Librerías de Python que necesita el bridge. |
| `setup/crear_python_portable.bat` | Crea un Python portable con todo lo necesario (opción B). |
| `scripts/` | Copias en texto de los scripts de la escena (respaldo legible). |

---

## Requisitos

- **CoppeliaSim Edu 4.10**
- **TIA Portal V19** y **PLCSIM Advanced V6.0**
- **Python 3.12** con `asyncua`, `pyzmq` y `cbor2` — se puede cubrir de dos formas (ver abajo)

---

## Instalación

### 1. Python para el bridge — elija una opción

**Opción A: ya tiene Python 3.12 instalado**

```bat
py -3.12 -m pip install -r requirements.txt
```

CoppeliaSim usará el Python del sistema; no hay que configurar nada más.

**Opción B: Python portable (no instala nada en el equipo)**

1. Descargue `python-portable.zip` desde la sección **Releases** de este repositorio y descomprímalo en la raíz del repo, de modo que quede `python\python.exe`.
   *(Alternativa: ejecute `setup\crear_python_portable.bat`, que lo genera descargando todo de internet.)*
2. Abra `usrset.txt` de CoppeliaSim (normalmente en `%APPDATA%\CoppeliaSim\`) y ajuste la línea:
   ```
   defaultPython = C:/ruta/al/repo/OAC-NivelLiquidos/python/python.exe
   ```
   Use barras `/` y la ruta real de su equipo.
3. Reinicie CoppeliaSim.

### 2. Bridge

Copie `bridge\OAC_PLC_Bridge.py` a la carpeta de add-ons de CoppeliaSim:

```
C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\addOns\
```

### 3. Proyecto PLC

En TIA Portal: **Project → Retrieve** y seleccione `plc\Project8.zap19`.

---

## Arranque de la demo (en este orden)

1. **PLCSIM Advanced**: inicie la instancia `PLC1` y póngala en **RUN**.
2. **TIA Portal**: compile y haga **Download to device**.
3. **PID_Level en automático**: en una Watch table, escriba `3` en `"PID_Level".Mode` y aplique con *Modify now*. Repítalo después de cada descarga.
4. **CoppeliaSim**: abra `scene\TankLevel.ttt` y dé **Play**. El bridge se conecta solo; revise la consola por mensajes `[OAC Bridge Add-on]`.
5. Pulse **Start** en el panel de la escena y ajuste el setpoint de nivel desde el HMI.

Qué debería ver: la válvula de entrada se abre, el líquido sube y el nivel se estabiliza cerca del setpoint. Al pulsar **Stop**, las salidas van a cero; al volver a **Start**, las válvulas recuperan su posición.

---

## Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| El bridge no conecta / `ConnectionRefusedError` | La IP del endpoint OPC UA al inicio de `OAC_PLC_Bridge.py` no coincide con la de la instancia de PLCSIM, o el servidor OPC UA no está activo en el PLC. |
| El nivel no se mueve aunque hay Start | `PID_Level` no está en modo automático (paso 3 de la demo). |
| Aviso en consola sobre `DB_Motor` | Normal: el bridge también soporta la escena del motor/banda. Si `DB_Motor` no existe en el PLC, lo avisa una vez y el tanque funciona igual. |
| CoppeliaSim no ejecuta el bridge | Falta Python o sus librerías. Revise la opción A o B de la instalación. |
