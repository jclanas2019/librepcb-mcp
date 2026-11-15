# Glosario de términos y siglas

Términos técnicos que aparecen en este servidor MCP, en su README y en el flujo de diseño de
placas con LibrePCB para JLCPCB.

## Índice

1. [Diseño de placas (PCB)](#1-diseño-de-placas-pcb)
2. [Revisiones](#2-revisiones)
3. [Ruteo automático](#3-ruteo-automático)
4. [Fabricación y montaje](#4-fabricación-y-montaje)
5. [Software](#5-software)
6. [Componentes y señales de la placa de ejemplo (esp32s3-sensores)](#6-componentes-y-señales-de-la-placa-de-ejemplo-esp32s3-sensores)

---

## 1. Diseño de placas (PCB)

| Término | Significado |
|---|---|
| **PCB** (*Printed Circuit Board*) | Placa de circuito impreso: la placa donde se sueldan los componentes |
| **EDA** (*Electronic Design Automation*) | Programas para diseñar electrónica. LibrePCB es uno, KiCad es otro |
| **Esquemático** | El dibujo eléctrico: qué componente se conecta con cuál |
| **Footprint** (huella) | La forma en cobre donde se suelda un componente: sus pads y su contorno |
| **Pad** | Zona de cobre expuesta donde se suelda un pin |
| **SMD** (*Surface Mount Device*) | Componente de montaje superficial: se suelda encima de la placa, sin perforarla |
| **THT** (*Through-Hole Technology*) | Componente con pines que atraviesan la placa, como las tiras de pines |
| **Red** (*net*) | Conjunto de pines que deben quedar conectados entre sí, por ejemplo GND o 3V3 |
| **Clase de red** (*net class*) | Grupo de redes con las mismas reglas de ancho y separación, por ejemplo "potencia" con pistas de 0.5 mm |
| **Pista** (*trace*) | Línea de cobre que une pads |
| **Vía** (*via*) | Agujero metalizado que pasa una pista de una capa a otra |
| **Unión** (*junction*) | Punto donde se juntan o doblan varias pistas |
| **Segmento de red** (*net segment*) | En LibrePCB, grupo de pistas, vías y uniones conectadas de una misma red |
| **Capa** (*layer*) | Nivel de cobre. Una placa de 2 capas tiene `top_cu` (arriba) y `bot_cu` (abajo); las de 4 o más tienen capas internas (`inner`) |
| **Plano de cobre / de tierra** (*plane*, *ground plane*) | Gran superficie de cobre conectada a una red (normalmente GND) que rellena el espacio libre |
| **Contorno** (*board outline*) | Borde de la placa, por donde se corta |
| **Zona prohibida** (*keepout*) | Área donde no puede ir cobre (o vías, o piezas), por ejemplo bajo la antena de un módulo WiFi |
| **Fiducial** | Círculo de cobre que usan las máquinas de montaje para alinearse con la placa |
| **Agujero de montaje** (*mounting hole*) | Agujero para atornillar la placa |
| **NPTH** / **PTH** | Agujero sin metalizar (*Non-Plated Through Hole*) / agujero metalizado (*Plated Through Hole*) |
| **Ranura** (*slot*) | Agujero alargado, como los que sujetan la carcasa de un conector USB-C |
| **Anillo anular** (*annular ring*) | Corona de cobre que rodea un agujero metalizado o una vía |
| **Serigrafía** (*silkscreen*, *legend*) | Textos y dibujos impresos sobre la placa (R1, U3...) |
| **Máscara antisoldante** (*solder mask*, *stop mask*) | Barniz (normalmente verde) que cubre el cobre, salvo los pads |
| **Vía tapada** (*tented via*) | Vía cubierta por la máscara antisoldante |
| **Pasta de soldar** (*solder paste*) | Pasta de estaño que se aplica sobre los pads antes de montar los componentes |
| **Courtyard** | Área reservada alrededor de cada componente para que no choque con otro |
| **Separación** (*clearance*) | Distancia mínima entre dos cobres de redes distintas, o entre cobre y borde |
| **Ancho de pista** (*trace width*) | Grosor de la pista. Más ancho permite más corriente |
| **Estrechamiento** (*neck-down*) | Tramo final más delgado de una pista ancha para poder entrar en un pad pequeño |
| **Paso** (*pitch*) | Distancia entre centros de pines vecinos, por ejemplo 0.5 mm en chips muy finos |
| **Puente de soldadura** (*solder jumper*) | Dos pads muy juntos que se unen o separan con estaño para activar una opción |
| **Pad de prueba** (*test pad*, *test point*) | Punto de cobre para medir con el multímetro u osciloscopio |
| **Antena PCB** | Antena hecha con una pista de cobre dentro del módulo; necesita una zona sin cobre debajo |
| **Ocupación** | Porcentaje del área útil de la placa que ocupan las piezas. Más del 50 % hace difícil el ruteo |

## 2. Revisiones

| Término | Significado |
|---|---|
| **ERC** (*Electrical Rule Check*) | Revisión eléctrica del esquemático: pines sin conectar, redes mal nombradas, etc. |
| **DRC** (*Design Rule Check*) | Revisión de reglas de la placa: pistas muy juntas o delgadas, cobre al borde, vías que chocan, conexiones que faltan |
| **Error / aviso** (*error / warning*) | En el DRC, un error impide fabricar; un aviso conviene revisarlo pero no bloquea |
| **Mensaje aprobado** (*approved message*) | Aviso del DRC/ERC que el diseñador revisó y aceptó a propósito |
| **Sin rutear** (*unrouted*, *airwire*) | Conexión que existe en el esquemático pero aún no tiene pista en la placa |
| **Preflight** | Revisión previa que hace este servidor antes de rutear, para detectar problemas a tiempo |
| **Production check** | Revisión final de este servidor antes de generar los archivos de fabricación |
| **Perfil de fabricante** (*fab profile*) | Conjunto de reglas mínimas (ancho, separación, taladros) que acepta un fabricante |

## 3. Ruteo automático

| Término | Significado |
|---|---|
| **Rutear** (*routing*) | Dibujar las pistas que conectan los componentes |
| **Autorouter** | Programa que rutea automáticamente |
| **Freerouting** | Autorouter gratuito y de código abierto escrito en Java |
| **Specctra** | Formato estándar de la industria para pasar una placa a un autorouter y traer el resultado de vuelta |
| **DSN** (*Specctra Design file*) | Archivo de entrada del autorouter: piezas, pads, redes y reglas |
| **SES** (*Specctra Session file*) | Archivo de salida del autorouter: las pistas y vías resultantes |
| **Etapa** (*pass*, *stage*) | Cada ronda del ruteo. Este servidor usa 3: anchos completos, completar con pistas finas y rehacer cerca de lo que falta |
| **Rip-up and reroute** | Borrar parte del ruteo y volver a hacerlo para abrir espacio |
| **Ruteo protegido** (*protected wiring*) | Pistas ya hechas que el autorouter no puede mover en la etapa siguiente |
| **Variante** | Otro intento de ruteo con distinta separación o ancho de señal |
| **Ajuste fino** (*nudge*) | Corrección automática de pocas centésimas de milímetro (vías muy juntas, pistas al borde) |
| **Java / JRE** (*Java Runtime Environment*) | Entorno necesario para ejecutar programas Java como Freerouting |
| **Temurin / Adoptium** | Distribución gratuita de Java que usa este servidor |
| **Proceso en segundo plano** (*background worker*) | Programa que sigue rutando mientras Claude responde; su avance se ve con `autoroute_status` |

## 4. Fabricación y montaje

| Término | Significado |
|---|---|
| **JLCPCB** | Fabricante de placas que además puede entregarlas con los componentes montados |
| **LCSC** | Distribuidor de componentes asociado a JLCPCB. El código LCSC (por ejemplo C15127) identifica cada pieza en su catálogo |
| **Pieza Basic / Extended** | En JLCPCB, las Basic están siempre cargadas en las máquinas y no tienen recargo; cada tipo de pieza Extended cobra un cargo extra de montaje |
| **MPN** (*Manufacturer Part Number*) | Número de parte del fabricante del componente, por ejemplo AO3401A |
| **PCBA** (*PCB Assembly*) | Placa con los componentes ya montados, o el servicio de montarlos |
| **Gerber** | Formato estándar de los archivos de fabricación: un archivo por capa (cobre, máscara, serigrafía, contorno) |
| **Archivo de taladros** (*drill file*, `.drl`) | Posición y diámetro de cada agujero (formato Excellon) |
| **BOM** (*Bill of Materials*) | Lista de materiales: qué componentes van y cuántos |
| **CPL** (*Component Placement List*) / **Pick & Place** | Posición, capa y rotación de cada componente, para la máquina de montaje |
| **iBOM** (*interactive BOM*) | Página web que muestra dónde va cada componente en la placa |
| **DNP** (*Do Not Populate*) | "No montar": piezas que la fábrica no suelda (pines a mano, puentes, pads de prueba) |
| **SMT** (*Surface Mount Technology*) | Proceso de montaje automático de componentes SMD |
| **IPC-D-356** | Archivo de conexiones que usa la fábrica para probar eléctricamente la placa sin componentes |
| **STEP** | Formato de modelo 3D, útil para diseñar carcasas |
| **oz** (onzas de cobre) | Grosor del cobre: 1 oz son unos 35 µm |
| **0402 / 0603 / 0805 / 1206** | Tamaños estándar de resistencias y condensadores SMD en pulgadas: 0603 son 0.06 × 0.03 pulgadas (1.6 × 0.8 mm) |
| **SOT-23, SOD, LGA, PLCC** | Tipos de encapsulado de componentes (pequeño transistor, diodo, matriz de pads bajo el chip, LED) |

## 5. Software

| Término | Significado |
|---|---|
| **MCP** (*Model Context Protocol*) | Estándar que permite a Claude usar herramientas externas |
| **Servidor MCP** | Programa (aquí en Python) que ofrece herramientas a Claude Desktop, como `run_drc` o `autoroute` |
| **FastMCP** | Librería de Python para escribir servidores MCP |
| **Claude Desktop** | La aplicación de escritorio de Claude, que arranca y usa los servidores MCP |
| **CLI** (*Command-Line Interface*) | Programa que se usa desde la Terminal. `librepcb-cli` hace ERC, DRC y exportaciones sin abrir LibrePCB |
| **GUI** (*Graphical User Interface*) | Interfaz con ventanas: LibrePCB abierto normalmente |
| **S-expression** | Formato de texto con paréntesis en que LibrePCB guarda sus archivos |
| **`.lp`** | Archivo de datos de LibrePCB (placa, circuito, esquemático, librería) |
| **`.lpp`** | Archivo principal de un proyecto de LibrePCB |
| **`.lock`** | Archivo que crea LibrePCB mientras un proyecto está abierto, para que no se modifique desde otro lado a la vez |
| **Bloqueo viejo** (*stale lock*) | `.lock` que quedó de una sesión que se cerró mal; el programa ya no está abierto |
| **UUID** | Identificador único (`a1b2c3d4-...`) que LibrePCB asigna a cada objeto |
| **Librería local** | Copia de los símbolos, componentes y footprints dentro de la carpeta del proyecto (`library/`) |
| **Entorno virtual** (`.venv`) | Copia aislada de Python con las librerías del servidor, para no tocar el Python del sistema |
| **stdout / stderr** | Salida normal / salida de errores de un programa. El servidor MCP usa stdout para hablar con Claude |
| **JSON** | Formato de texto para datos estructurados (configuración, estado del ruteo) |
| **Copia de seguridad** (*backup*) | Copia de un archivo antes de cambiarlo, en `output/mcp-backups/` |
| **Skill** | Instrucciones guardadas que Claude sigue para un tipo de tarea (aquí, `SKILL.md`) |

## 6. Componentes y señales de la placa de ejemplo (esp32s3-sensores)

| Término | Significado |
|---|---|
| **ESP32-S3** | Microcontrolador con WiFi y Bluetooth (módulo WROOM-1, U1) |
| **LDO** (*Low-Dropout regulator*) | Regulador lineal que baja el voltaje a 3.3 V (U2, TLV757) |
| **Cargador LiPo** | Circuito que carga la batería de litio-polímero (U3, MCP73831) |
| **LiPo** | Batería de litio-polímero |
| **ESD** (*Electrostatic Discharge*) | Descarga electrostática; U4 (USBLC6) protege las líneas del USB |
| **Polifusible** (*PTC*) | Fusible que se rearma solo al enfriarse (F1) |
| **Diodo Schottky** | Diodo de baja caída de voltaje, usado para cambiar entre USB y batería (D1) |
| **MOSFET** | Transistor usado como interruptor (Q1, Q2) |
| **USB-C** | Conector USB reversible (J1) |
| **D+ / D−** (`USB_DP`, `USB_DN`) | Par de datos del USB |
| **CC1 / CC2** | Pines del USB-C que, con resistencias de 5.1 kΩ, le indican al cargador que entregue 5 V |
| **I2C** | Bus de 2 cables por el que el ESP32 habla con los sensores |
| **SDA / SCL** | Línea de datos / línea de reloj del I2C |
| **Pull-up** | Resistencia que mantiene una línea en nivel alto cuando nadie la activa (R16, R17) |
| **Qwiic** | Conector estándar de 4 pines para sensores I2C (J3) |
| **IMU** (*Inertial Measurement Unit*) | Sensor de movimiento: acelerómetro y giroscopio (U6, LSM6DS3TR-C) |
| **BME280** | Sensor de temperatura, humedad y presión (U5) |
| **VEML7700** | Sensor de luz ambiental (U7) |
| **WS2812B** | LED RGB direccionable con un solo cable de datos (LED1) |
| **Level shifter** | Adaptador de nivel lógico de 3.3 V a 5 V para el LED (U8, SN74LV1T34) |
| **Buzzer** | Zumbador (BZ1) |
| **ADC** (*Analog-to-Digital Converter*) | Convertidor que lee voltajes; aquí mide el voltaje de la batería |
| **VBUS** | 5 V que llegan por el USB |
| **VBAT / BAT+** | Voltaje de la batería |
| **VSYS** | Alimentación del sistema: USB o batería, según cuál esté conectado |
| **3V3** | 3.3 V regulados que alimentan el ESP32 y los sensores |
| **GND** | Tierra: la referencia de 0 V |
| **Designador** (*designator*, *reference*) | Nombre de cada pieza en la placa: R = resistencia, C = condensador, U = circuito integrado, J = conector, Q = transistor, D = diodo, SW = pulsador, LED, BZ = buzzer, F = fusible, JP = puente, TP = punto de prueba |
