"""
OAC_PLC_Bridge.py

CoppeliaSim Python ADD-ON version of the OAC <-> PLC bridge.

Unlike the original oac_plc_bridge.py (a separate script run manually
from a terminal via the ZMQ Remote API), this version runs INSIDE
CoppeliaSim itself, using the native 'sim' API directly -- no ZMQ
hop needed. It starts automatically when CoppeliaSim launches, and
its actual PLC communication is active ONLY while a simulation is
playing (sysCall_actuation), staying idle otherwise.

Flujo de señales:
  CoppeliaSim (botones) --signal.oac_pressed--> PLC (I_Start/I_Stop)
  PLC (lógica interna) --calcula--> Q_Lamp --> CoppeliaSim (lámpara)

  PLC (DB_Tank.V_In) --> CoppeliaSim (TankSystem: rotación de
    Valve_In) -- el PID del PLC comanda la válvula proporcional de
    entrada, igual que en una planta real.
  CoppeliaSim (PlantHMI: sliders V_Out y SP_Level, operados por el
    usuario) --> PLC (DB_Tank.V_Out / DB_Tank.SP_Level) -- el HMI
    es la estación de operador que envía ajustes al PLC (como
    cualquier HMI/SCADA real); el PLC sigue siendo quien controla
    el proceso. V_Out también fluye de vuelta PLC -> CoppeliaSim,
    para reflejar en el HMI el enclavamiento de Start/Stop del PLC
    (que puede forzar V_Out a 0 sin que el operador lo pida).
  CoppeliaSim (TankSystem: nivel simulado del líquido) -->
    PLC (DB_Tank.L_Level) -- CoppeliaSim simula la física del
    tanque (cómo responde el nivel a las válvulas) y el puente
    relé ese valor hacia el PLC como si fuera la lectura de un
    sensor de nivel real -- el PLC nunca calcula el nivel él mismo.
  CoppeliaSim (PlantHMI: ventana "Technician Settings", constantes
    Kp/Ti/Td) --> PLC ("PID_Level".Retain.CtrlParams.Gain/Ti/Td) --
    ajuste de sintonía del PID desde un panel técnico separado.

  PLC (DB_Motor.V_Cmd, salida del PID de velocidad) --> CoppeliaSim
    (/Motor: voltaje de comando del motor DC, 0-100%).
  CoppeliaSim (/conveyor: velocidad simulada de la banda, m/s) -->
    PLC (DB_Motor.Speed_PV) -- el PLC comanda el motor y lee la
    velocidad real como un sensor, igual que con el tanque.
  CoppeliaSim (MotorHMI: consigna de velocidad, constantes Kp/Ti/Td)
    --> PLC (DB_Motor.Speed_SP, "PID_Speed".Retain.CtrlParams.*) --
    el HMI es la estación de operador/técnico, igual que PlantHMI.

INSTALACIÓN
-------------
1. Copie este archivo dentro de la carpeta:
       <carpeta de instalación de CoppeliaSim>/addOns/
   (créela si no existe -- ej.:
       C:\\Program Files\\CoppeliaRobotics\\CoppeliaSimEdu\\addOns\\)

2. Confirme que el intérprete de Python que CoppeliaSim usa (el
   configurado como 'defaultPython' en usrset.txt, normalmente
   Preferencias/User settings) tiene 'asyncua' instalado.

3. Reinicie CoppeliaSim. El add-on aparece listado en
   [Menu bar > Modules] y arranca automáticamente -- pero solo
   ejecuta su lógica de comunicación con el PLC mientras la
   simulación está en Play (sysCall_actuation). Mientras la
   simulación está detenida, no hace nada (sysCall_nonSimulation).

4. IMPORTANTE: con este add-on activo, NO corra también
   oac_plc_bridge.py manualmente por terminal al mismo tiempo --
   ambos abrirían sesiones OPC UA independientes escribiendo sobre
   los mismos nodos, compitiendo entre sí. Use uno u otro.

5. Requisitos de la escena:
   - /PB_Start, /PB_Stop, /PL_Green (ControlPanel)
   - /TankSystem/Valve_In, /TankSystem/Valve_Out, /TankSystem/Tank
     (TankSystem) -- si estos no existen, el add-on sigue
     funcionando igual para Start/Stop/Lamp, simplemente omite la
     sincronización del tanque con una advertencia en consola.

CONFIGURACIÓN
---------------
Ajuste las constantes de la sección CONFIGURACIÓN más abajo según
su entorno (URL del servidor OPC UA, NodeIds, rutas de objetos).
"""

import time

try:
    from asyncua.sync import Client
    from asyncua import ua
except ImportError:
    Client = None
    ua = None


# ----------------------------------------------------------------
# CONFIGURACIÓN -- ajusta estos valores a tu entorno
# ----------------------------------------------------------------

PLC_URL = "opc.tcp://192.168.0.10:4840"

# ControlPanel (Start/Stop/Lamp)
NODE_I_START = 'ns=3;s="Data_block_1"."I_Start"'
NODE_I_STOP  = 'ns=3;s="Data_block_1"."I_Stop"'
NODE_Q_LAMP  = 'ns=3;s="Data_block_1"."Q_Lamp"'

BUTTON_START_PATH = "/PB_Start"
BUTTON_STOP_PATH = "/PB_Stop"
LAMP_PATH = "/PL_Green"

# TankSystem
NODE_V_IN    = 'ns=3;s="DB_Tank"."V_In"'
NODE_V_OUT   = 'ns=3;s="DB_Tank"."V_Out"'
NODE_L_LEVEL = 'ns=3;s="DB_Tank"."L_Level"'
NODE_SP_LEVEL = 'ns=3;s="DB_Tank"."SP_Level"'

# PID tuning constants (technician settings) -- live inside the
# PID_Compact technology object's own instance DB, under
# Retain.CtrlParams, confirmed via TIA Portal's Watch table
# autocomplete. One-directional: CoppeliaSim (Technician Settings
# window) -> PLC only.
NODE_PID_KP = 'ns=3;s="PID_Level"."Retain"."CtrlParams"."Gain"'
NODE_PID_TI = 'ns=3;s="PID_Level"."Retain"."CtrlParams"."Ti"'
NODE_PID_TD = 'ns=3;s="PID_Level"."Retain"."CtrlParams"."Td"'

TANK_VALVE_IN_PATH  = "/TankSystem/Valve_In"
TANK_VALVE_OUT_PATH = "/TankSystem/Valve_Out"
TANK_PATH            = "/TankSystem/Tank"

# Motor DC + banda transportadora (ConveyorSystem)
#   V_Cmd    : PLC -> CoppeliaSim (el PID de velocidad del PLC comanda
#              el voltaje del motor, 0-100%).
#   Speed_PV : CoppeliaSim -> PLC (velocidad simulada de la banda, m/s,
#              como lectura de un sensor de velocidad).
# Speed_SP (consigna) vive solo en el PLC por ahora (se ajusta con una
# Watch table); no hay control de HMI para ella todavía.
#   Speed_SP : CoppeliaSim (MotorHMI, operador) -> PLC. La consigna se
#              fija en el HMI y se guarda como propiedad
#              signal.oac_setpoint del objeto /conveyor.
#   Kp/Ti/Td : CoppeliaSim (MotorHMI, ventana técnica) -> PLC, hacia el
#              PID_Speed. Se guardan en /Motor como signal.oac_kp/ti/td.
NODE_V_CMD    = 'ns=3;s="DB_Motor"."V_Cmd"'
NODE_SPEED_PV = 'ns=3;s="DB_Motor"."Speed_PV"'
NODE_SPEED_SP = 'ns=3;s="DB_Motor"."Speed_SP"'

NODE_PID_SPEED_KP = 'ns=3;s="PID_Speed"."Retain"."CtrlParams"."Gain"'
NODE_PID_SPEED_TI = 'ns=3;s="PID_Speed"."Retain"."CtrlParams"."Ti"'
NODE_PID_SPEED_TD = 'ns=3;s="PID_Speed"."Retain"."CtrlParams"."Td"'

MOTOR_PATH    = "/Motor"       # signal.oac_value = V_Cmd
CONVEYOR_PATH = "/conveyor"    # signal.oac_value = Speed_PV

# Reintentos de conexión al iniciar la simulación, por si PLCSIM
# Advanced todavía no terminó de levantar el servidor OPC UA.
CONNECT_RETRIES = 5
CONNECT_RETRY_DELAY_S = 1.0


# ----------------------------------------------------------------
# Estado interno del add-on (persistente entre callbacks)
# ----------------------------------------------------------------

_state = {
    "plc_client": None,
    "connected": False,

    # ControlPanel (optional -- may stay False if the scene lacks it)
    "panel_available": False,
    "h_start": None,
    "h_stop": None,
    "h_lamp": None,
    "node_start": None,
    "node_stop": None,
    "node_lamp": None,
    "last_lamp_state": None,

    # TankSystem (optional -- may stay None if the scene lacks it)
    "tank_available": False,
    "h_tank_valve_in": None,
    "h_tank_valve_out": None,
    "h_tank": None,
    "node_v_in": None,
    "node_v_out": None,
    "node_level": None,
    "node_sp_level": None,
    "last_v_in": None,
    "last_v_out_from_plc": None,
    "last_v_out_from_coppelia": None,
    "last_level": None,
    "last_sp_level": None,

    "node_pid_kp": None,
    "node_pid_ti": None,
    "node_pid_td": None,
    "last_kp": None,
    "last_ti": None,
    "last_td": None,

    # ConveyorSystem (optional -- may stay False if the scene lacks it)
    "motor_available": False,
    "motor_error_logged": False,
    "h_motor": None,
    "h_conveyor": None,
    "node_v_cmd": None,
    "node_speed_pv": None,
    "node_speed_sp": None,
    "node_pid_speed_kp": None,
    "node_pid_speed_ti": None,
    "node_pid_speed_td": None,
    "last_v_cmd": None,
    "last_speed_pv": None,
    "last_speed_sp": None,
    "last_motor_kp": None,
    "last_motor_ti": None,
    "last_motor_td": None,
}


def _write_bool(node, value):
    """
    Escribe un valor booleano en un nodo OPC UA sin timestamps,
    evitando BadWriteNotSupported en el servidor Siemens.
    """
    dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
    dv.SourceTimestamp = None
    dv.ServerTimestamp = None
    node.write_attribute(ua.AttributeIds.Value, dv)


def _write_float(node, value):
    """
    Escribe un valor Float (REAL de TIA Portal) en un nodo OPC UA
    sin timestamps, por la misma razón que _write_bool.
    """
    dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
    dv.SourceTimestamp = None
    dv.ServerTimestamp = None
    node.write_attribute(ua.AttributeIds.Value, dv)


def _connect_to_plc():
    """
    Intenta conectar al servidor OPC UA del PLC, con reintentos
    cortos por si PLCSIM Advanced aún está levantando el servidor.
    Devuelve True si conectó, False si no lo logró.
    """

    if Client is None:
        sim.addLog(
            sim.verbosity_scripterrors,
            "[OAC Bridge Add-on] 'asyncua' no está instalado en el "
            "intérprete de Python configurado en CoppeliaSim. "
            "Instálelo con: pip install asyncua"
        )
        return False

    for attempt in range(1, CONNECT_RETRIES + 1):

        try:

            client = Client(url=PLC_URL)
            client.connect()

            _state["plc_client"] = client
            _state["node_start"] = client.get_node(NODE_I_START)
            _state["node_stop"] = client.get_node(NODE_I_STOP)
            _state["node_lamp"] = client.get_node(NODE_Q_LAMP)

            if _state["tank_available"]:
                _state["node_v_in"] = client.get_node(NODE_V_IN)
                _state["node_v_out"] = client.get_node(NODE_V_OUT)
                _state["node_level"] = client.get_node(NODE_L_LEVEL)
                _state["node_sp_level"] = client.get_node(NODE_SP_LEVEL)
                _state["node_pid_kp"] = client.get_node(NODE_PID_KP)
                _state["node_pid_ti"] = client.get_node(NODE_PID_TI)
                _state["node_pid_td"] = client.get_node(NODE_PID_TD)

            if _state["motor_available"]:
                _state["node_v_cmd"] = client.get_node(NODE_V_CMD)
                _state["node_speed_pv"] = client.get_node(NODE_SPEED_PV)
                _state["node_speed_sp"] = client.get_node(NODE_SPEED_SP)
                _state["node_pid_speed_kp"] = client.get_node(NODE_PID_SPEED_KP)
                _state["node_pid_speed_ti"] = client.get_node(NODE_PID_SPEED_TI)
                _state["node_pid_speed_td"] = client.get_node(NODE_PID_SPEED_TD)

            _state["connected"] = True

            sim.addLog(
                sim.verbosity_scriptinfos,
                "[OAC Bridge Add-on] Conectado al PLC en " + PLC_URL
            )

            return True

        except Exception as e:

            sim.addLog(
                sim.verbosity_scriptwarnings,
                "[OAC Bridge Add-on] Intento %d/%d fallido: %s" % (
                    attempt, CONNECT_RETRIES, str(e)
                )
            )

            time.sleep(CONNECT_RETRY_DELAY_S)

    sim.addLog(
        sim.verbosity_scripterrors,
        "[OAC Bridge Add-on] No se pudo conectar al PLC tras "
        + str(CONNECT_RETRIES) + " intentos. El puente no estará "
        "activo en esta simulación."
    )

    return False


def _disconnect_from_plc():

    client = _state.get("plc_client")

    if client is not None:

        try:
            client.disconnect()
        except Exception:
            pass

    _state["plc_client"] = None
    _state["connected"] = False
    _state["node_start"] = None
    _state["node_stop"] = None
    _state["node_lamp"] = None
    _state["last_lamp_state"] = None
    _state["node_v_in"] = None
    _state["node_v_out"] = None
    _state["node_level"] = None
    _state["node_sp_level"] = None
    _state["last_v_in"] = None
    _state["last_v_out_from_plc"] = None
    _state["last_v_out_from_coppelia"] = None
    _state["last_level"] = None
    _state["last_sp_level"] = None
    _state["node_pid_kp"] = None
    _state["node_pid_ti"] = None
    _state["node_pid_td"] = None
    _state["last_kp"] = None
    _state["last_ti"] = None
    _state["last_td"] = None
    _state["node_v_cmd"] = None
    _state["node_speed_pv"] = None
    _state["node_speed_sp"] = None
    _state["node_pid_speed_kp"] = None
    _state["node_pid_speed_ti"] = None
    _state["node_pid_speed_td"] = None
    _state["last_v_cmd"] = None
    _state["last_speed_pv"] = None
    _state["last_speed_sp"] = None
    _state["last_motor_kp"] = None
    _state["last_motor_ti"] = None
    _state["last_motor_td"] = None
    _state["motor_error_logged"] = False


# ----------------------------------------------------------------
# Callbacks del ciclo de vida del add-on
# ----------------------------------------------------------------

def sysCall_init():

    global sim
    sim = require('sim')

    sim.addLog(
        sim.verbosity_scriptinfos,
        "[OAC Bridge Add-on] Cargado. Esperando inicio de simulación."
    )


def sysCall_beforeSimulation():

    _state["h_start"] = sim.getObject(BUTTON_START_PATH, {"noError": True})
    _state["h_stop"] = sim.getObject(BUTTON_STOP_PATH, {"noError": True})
    _state["h_lamp"] = sim.getObject(LAMP_PATH, {"noError": True})

    missing = [
        path for handle, path in (
            (_state["h_start"], BUTTON_START_PATH),
            (_state["h_stop"], BUTTON_STOP_PATH),
            (_state["h_lamp"], LAMP_PATH),
        ) if handle in (None, -1)
    ]

    # ------------------------------------------------------------
    # Every subsystem (ControlPanel, TankSystem, Motor/conveyor) is
    # optional: each scene may contain any combination of them. A
    # missing one is skipped with a warning; the bridge only refuses
    # to start if NONE of them is present (checked below).
    # ------------------------------------------------------------

    if missing:

        _state["panel_available"] = False

        sim.addLog(
            sim.verbosity_scriptwarnings,
            "[OAC Bridge Add-on] ControlPanel no encontrado (" +
            ", ".join(missing) + ") -- se omite Start/Stop/Lámpara."
        )

    else:

        _state["panel_available"] = True

    # ------------------------------------------------------------
    # TankSystem is optional: if it's not in the scene, the other
    # subsystems still work normally -- we just skip the tank sync
    # with a warning instead of aborting everything.
    # ------------------------------------------------------------

    _state["h_tank_valve_in"] = sim.getObject(TANK_VALVE_IN_PATH, {"noError": True})
    _state["h_tank_valve_out"] = sim.getObject(TANK_VALVE_OUT_PATH, {"noError": True})
    _state["h_tank"] = sim.getObject(TANK_PATH, {"noError": True})

    tank_missing = [
        path for handle, path in (
            (_state["h_tank_valve_in"], TANK_VALVE_IN_PATH),
            (_state["h_tank_valve_out"], TANK_VALVE_OUT_PATH),
            (_state["h_tank"], TANK_PATH),
        ) if handle in (None, -1)
    ]

    if tank_missing:

        _state["tank_available"] = False

        sim.addLog(
            sim.verbosity_scriptwarnings,
            "[OAC Bridge Add-on] TankSystem no encontrado (" +
            ", ".join(tank_missing) + ") -- se omite su sincronización."
        )

    else:

        _state["tank_available"] = True

    # ------------------------------------------------------------
    # ConveyorSystem (Motor + conveyor) is optional too, for the
    # same reason as the tank.
    # ------------------------------------------------------------

    _state["h_motor"] = sim.getObject(MOTOR_PATH, {"noError": True})
    _state["h_conveyor"] = sim.getObject(CONVEYOR_PATH, {"noError": True})

    motor_missing = [
        path for handle, path in (
            (_state["h_motor"], MOTOR_PATH),
            (_state["h_conveyor"], CONVEYOR_PATH),
        ) if handle in (None, -1)
    ]

    if motor_missing:

        _state["motor_available"] = False

        sim.addLog(
            sim.verbosity_scriptwarnings,
            "[OAC Bridge Add-on] Motor/banda no encontrados (" +
            ", ".join(motor_missing) + ") -- se omite su sincronización."
        )

    else:

        _state["motor_available"] = True

    if not (_state["panel_available"] or _state["tank_available"]
            or _state["motor_available"]):

        sim.addLog(
            sim.verbosity_scripterrors,
            "[OAC Bridge Add-on] No se encontró ningún subsistema OAC en "
            "la escena (ControlPanel, TankSystem, Motor/conveyor) -- "
            "el puente no se activará."
        )

        return

    _connect_to_plc()


def sysCall_actuation():

    if not _state["connected"]:
        return

    try:

        # ControlPanel (Start/Stop/Lámpara) -- solo si está en la escena.
        # Si no está, I_Start/I_Stop no se tocan desde aquí, así que
        # pueden operarse a mano desde una Watch table del PLC.
        if _state["panel_available"]:

            pressed_start = sim.getBoolProperty(
                _state["h_start"], "signal.oac_pressed", {"noError": True}
            ) or False

            pressed_stop = sim.getBoolProperty(
                _state["h_stop"], "signal.oac_pressed", {"noError": True}
            ) or False

            _write_bool(_state["node_start"], pressed_start)
            _write_bool(_state["node_stop"], pressed_stop)

            lamp_on = _state["node_lamp"].read_value()

            if lamp_on != _state["last_lamp_state"]:

                sim.setBoolProperty(
                    _state["h_lamp"], "signal.oac_on", bool(lamp_on)
                )

                _state["last_lamp_state"] = lamp_on

                sim.addLog(
                    sim.verbosity_scriptinfos,
                    "[OAC Bridge Add-on] Q_Lamp = " + str(lamp_on)
                )

        # ----------------------------------------------------
        # TankSystem:
        #   V_In : PLC -> CoppeliaSim (el PID del PLC comanda la
        #     válvula de entrada; solo lectura desde CoppeliaSim).
        #   V_Out : bidireccional. El operador la ajusta en el HMI
        #     (PlantHMI) -> se relé hacia el PLC. El PLC también
        #     puede forzarla a 0 (enclavamiento Start/Stop) -> ese
        #     cambio se relé de vuelta hacia CoppeliaSim, para que
        #     el HMI refleje el valor real que el PLC está usando.
        #   SP_Level : CoppeliaSim (HMI) -> PLC únicamente (el
        #     operador fija el punto de consigna del PID).
        #   L_Level : CoppeliaSim -> PLC (sensor de nivel simulado).
        # ----------------------------------------------------

        if _state["tank_available"]:

            v_in = _state["node_v_in"].read_value()

            if v_in != _state["last_v_in"]:
                _state["last_v_in"] = v_in
                sim.setFloatProperty(
                    _state["h_tank_valve_in"], "signal.oac_value", float(v_in)
                )

            # --- V_Out: CoppeliaSim (operador) -> PLC ---
            local_v_out = sim.getFloatProperty(
                _state["h_tank_valve_out"], "signal.oac_value", {"noError": True}
            ) or 0.0

            if local_v_out != _state["last_v_out_from_coppelia"]:
                _state["last_v_out_from_coppelia"] = local_v_out
                _write_float(_state["node_v_out"], local_v_out)
                _state["last_v_out_from_plc"] = local_v_out

            # --- V_Out: PLC (p.ej. enclavamiento) -> CoppeliaSim ---
            plc_v_out = _state["node_v_out"].read_value()

            if plc_v_out != _state["last_v_out_from_plc"]:
                _state["last_v_out_from_plc"] = plc_v_out
                sim.setFloatProperty(
                    _state["h_tank_valve_out"], "signal.oac_value", float(plc_v_out)
                )
                _state["last_v_out_from_coppelia"] = plc_v_out

                sim.addLog(
                    sim.verbosity_scriptinfos,
                    "[OAC Bridge Add-on] V_Out recibido del PLC = %.2f" % plc_v_out
                )

            # --- SP_Level: CoppeliaSim (operador) -> PLC ---
            sp_level = sim.getFloatProperty(
                _state["h_tank"], "signal.oac_setpoint", {"noError": True}
            )

            if sp_level is not None and sp_level != _state["last_sp_level"]:
                _state["last_sp_level"] = sp_level
                _write_float(_state["node_sp_level"], sp_level)

            # --- L_Level: CoppeliaSim -> PLC (sensor) ---
            level = sim.getFloatProperty(
                _state["h_tank"], "signal.oac_value", {"noError": True}
            ) or 0.0

            if level != _state["last_level"]:
                _state["last_level"] = level
                _write_float(_state["node_level"], level)

                sim.addLog(
                    sim.verbosity_scriptinfos,
                    "[OAC Bridge Add-on] L_Level enviado al PLC = %.2f" % level
                )

            # --- PID tuning (Kp/Ti/Td): CoppeliaSim (Technician
            # Settings) -> PLC only. Own try/except so a problem
            # with this specific path (e.g. a different PID_Compact
            # version) doesn't break the rest of the tank sync.
            try:

                kp = sim.getFloatProperty(
                    _state["h_tank"], "signal.oac_kp", {"noError": True}
                )
                ti = sim.getFloatProperty(
                    _state["h_tank"], "signal.oac_ti", {"noError": True}
                )
                td = sim.getFloatProperty(
                    _state["h_tank"], "signal.oac_td", {"noError": True}
                )

                if kp is not None and kp != _state["last_kp"]:
                    _state["last_kp"] = kp
                    _write_float(_state["node_pid_kp"], kp)

                if ti is not None and ti != _state["last_ti"]:
                    _state["last_ti"] = ti
                    _write_float(_state["node_pid_ti"], ti)

                if td is not None and td != _state["last_td"]:
                    _state["last_td"] = td
                    _write_float(_state["node_pid_td"], td)

            except Exception as e:

                sim.addLog(
                    sim.verbosity_scriptwarnings,
                    "[OAC Bridge Add-on] Error enviando constantes PID: "
                    + str(e)
                )

        # ----------------------------------------------------
        # ConveyorSystem (motor DC + banda):
        #   V_Cmd : PLC -> CoppeliaSim. El PID de velocidad del PLC
        #     comanda el voltaje del motor (0-100%), igual que un
        #     controlador real comanda un variador/driver. Se
        #     escribe en Motor.signal.oac_value, que es lo que lee
        #     ConveyorSystem_simulationScript.lua.
        #   Speed_PV : CoppeliaSim -> PLC. La velocidad de la banda
        #     (m/s) la calcula la física del motor en CoppeliaSim y
        #     se publica en conveyor.signal.oac_value; el puente la
        #     relé al PLC como lectura de un sensor de velocidad.
        # Con su propio try/except (error avisado una sola vez) para
        # que un problema aquí -- p.ej. DB_Motor aún no descargado
        # al PLC -- no tumbe la sincronización de Start/Stop ni del
        # tanque.
        # ----------------------------------------------------

        if _state["motor_available"]:

            try:

                v_cmd = _state["node_v_cmd"].read_value()

                if v_cmd != _state["last_v_cmd"]:
                    _state["last_v_cmd"] = v_cmd
                    sim.setFloatProperty(
                        _state["h_motor"], "signal.oac_value", float(v_cmd)
                    )

                speed_pv = sim.getFloatProperty(
                    _state["h_conveyor"], "signal.oac_value", {"noError": True}
                ) or 0.0

                if speed_pv != _state["last_speed_pv"]:
                    _state["last_speed_pv"] = speed_pv
                    _write_float(_state["node_speed_pv"], speed_pv)

                # --- Speed_SP: CoppeliaSim (HMI, operador) -> PLC ---
                speed_sp = sim.getFloatProperty(
                    _state["h_conveyor"], "signal.oac_setpoint", {"noError": True}
                )

                if speed_sp is not None and speed_sp != _state["last_speed_sp"]:
                    _state["last_speed_sp"] = speed_sp
                    _write_float(_state["node_speed_sp"], speed_sp)

                # Funcionó: si luego vuelve a fallar, que avise otra vez.
                _state["motor_error_logged"] = False

            except Exception as e:

                if not _state["motor_error_logged"]:

                    _state["motor_error_logged"] = True

                    sim.addLog(
                        sim.verbosity_scriptwarnings,
                        "[OAC Bridge Add-on] Error sincronizando motor/banda "
                        "(¿DB_Motor descargado al PLC?): " + str(e)
                    )

            # --- Sintonía del PID_Speed (Kp/Ti/Td): CoppeliaSim
            # (ventana técnica del MotorHMI) -> PLC. Con su propio
            # try/except: si la ruta interna del bloque tecnológico
            # fallara, no afecta el control del motor. last_* se
            # actualiza ANTES de escribir, así un fallo avisa una sola
            # vez por cambio en vez de repetirse en cada paso.
            try:

                kp = sim.getFloatProperty(
                    _state["h_motor"], "signal.oac_kp", {"noError": True}
                )
                ti = sim.getFloatProperty(
                    _state["h_motor"], "signal.oac_ti", {"noError": True}
                )
                td = sim.getFloatProperty(
                    _state["h_motor"], "signal.oac_td", {"noError": True}
                )

                if kp is not None and kp != _state["last_motor_kp"]:
                    _state["last_motor_kp"] = kp
                    _write_float(_state["node_pid_speed_kp"], kp)

                if ti is not None and ti != _state["last_motor_ti"]:
                    _state["last_motor_ti"] = ti
                    _write_float(_state["node_pid_speed_ti"], ti)

                if td is not None and td != _state["last_motor_td"]:
                    _state["last_motor_td"] = td
                    _write_float(_state["node_pid_speed_td"], td)

            except Exception as e:

                sim.addLog(
                    sim.verbosity_scriptwarnings,
                    "[OAC Bridge Add-on] Error enviando sintonía del "
                    "PID_Speed: " + str(e)
                )

    except Exception as e:

        sim.addLog(
            sim.verbosity_scriptwarnings,
            "[OAC Bridge Add-on] Error en el paso de sincronización: "
            + str(e)
        )


def sysCall_afterSimulation():

    _disconnect_from_plc()

    sim.addLog(
        sim.verbosity_scriptinfos,
        "[OAC Bridge Add-on] Simulación detenida, desconectado del PLC."
    )


def sysCall_nonSimulation():
    # Nada que hacer mientras la simulación está detenida.
    pass


def sysCall_cleanup():
    _disconnect_from_plc()
