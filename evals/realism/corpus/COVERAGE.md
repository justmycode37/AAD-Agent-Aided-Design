# Frozen object checklist coverage

`tasks.json` is the versioned task corpus. Each checklist item cites one or more public fact sources by ID. The source records contain direct URLs, retrieval date, and page/section citations. Reviewers receive these sources with the item prompt; sources guide the rubric but do not provide hidden model geometry.

## Audi 0B5 / DL501 dual-clutch transmission

The public manufacturer training source identifies a seven-speed, full-synchromesh, electro-hydraulically controlled transmission with two oil-cooled multi-plate clutches. It documents the K1 odd-gear path, K2 even/reverse path, hollow second input shaft, common output and center differential, front side-shaft path, and separate ATF and gear-oil systems with seals. It also labels the mechatronic unit, cooler, oil-level/fill access, drain, vents, shafts, selector rails and attachment points. See [Audi SSP 950143](https://content.datarunners.net/content/SSP/950143.pdf), especially pp. 6-14 and 28-31.

Checklist items cover `housing_and_case_joint`, `engine_input_interface`, `dual_clutch_pack`, `odd_even_input_shafts`, `gearsets_and_synchronizers`, `bearing_and_seal_interfaces`, `common_output_and_center_diff`, `rear_output_interface`, `front_final_drive_interface`, `mechatronic_module`, `separate_lubrication_systems`, and `cooler_and_service_ports`.

The 3D reference slot is empty. The prior search record in [the DSG-0B5 study](../../dsg-0b5/README.md) also states that no free, licence-usable CAD of a real car dual-clutch gearbox was found. Public cutaway drawings and photos are useful for locating visible systems, but outline/silhouette overlap is not a 3D accuracy measure and is not part of this harness.

## Bitcraze Crazyflie 2.1 nano quadcopter

The [Crazyflie 2.1 datasheet](https://www.bitcraze.io/documentation/hardware/crazyflie_2_1/crazyflie_2_1-datasheet.pdf) documents the dual-MCU controller, IMU/barometer, charging and expansion functions. Bitcraze's [assembly guide](https://www.bitcraze.io/documentation/tutorials/getting-started-with-crazyflie-2-x/) identifies the control board, motor mounts, clockwise and counter-clockwise propellers, LiPo battery, battery-holder deck and expansion pins. Bitcraze's [FAQ](https://www.bitcraze.io/support/f-a-q/) says the PCB is the frame. The [mesh documentation](https://www.bitcraze.io/documentation/repository/crazyflie-simulation/main/functional_areas/meshes/) says the mesh is low-polygon and provides component placement transforms.

Checklist items cover `pcb_structural_frame`, `four_motor_mount_interfaces`, `four_propellers`, `battery_and_holder`, `controller_and_sensor_envelope`, `motor_power_and_control_paths`, `expansion_deck_mount_and_bus`, `usb_service_interface`, and `assembly_and_replacement_access`.

The included `cf2_assembly.stl` is the Bitcraze Crazyflie 2.1 simulation mesh at pinned revision `38388c9da728fa98a2f00f195d55eed484e85d11`. The upstream repository's [MIT license](https://github.com/bitcraze/crazyflie-simulation/blob/38388c9da728fa98a2f00f195d55eed484e85d11/LICENSE) is retained beside the file. Its only approved use here is limited visible layout/orientation reference. The upstream documentation calls it simplified; it is not exact production CAD and does not provide hidden electronics, fit, tolerances, material or performance evidence.

## Serviceable Raspberry Pi 4 Model B enclosure

The task asks for a serviceable two-piece enclosure designed around a real board. Raspberry Pi's [specification page](https://www.raspberrypi.com/products/raspberry-pi-4-model-b/specifications/) lists approximate board dimensions and USB, Ethernet, USB-C power, dual micro-HDMI, audio, GPIO, CSI/DSI and microSD interfaces. Its [dimensioned mechanical drawing](https://pip-assets.raspberrypi.com/categories/545-raspberry-pi-4-model-b/documents/RP-008343-DS-1-raspberry-pi-4-mechanical-drawing.pdf) supplies board-hole and connector positions; the drawing notes that dimensions are approximate. The [product portal](https://pip.raspberrypi.com/categories/545-raspberry-pi-4-model-b) indexes the public documents.

Checklist items cover `enclosure_cavity_and_wall`, `board_mounting_pattern`, `usb_ethernet_port_wall`, `power_video_audio_ports`, `microsd_and_gpio_access`, `camera_display_ribbon_access`, `lid_fastener_interface`, `internal_clearance_and_retention`, and `wall_strength_and_opening_edges`.

The official board drawing is a 2D PDF, not an enclosure model. Raspberry Pi's [licensing page](https://www.raspberrypi.com/licensing/) distinguishes online documentation, product PDFs and design files; the reviewed product PDFs are generally CC BY-ND. Third-party cases reviewed for this task were non-commercial, had an unclear Creative Commons variant, or targeted a different board. No file was downloaded or stored, and the 3D slot stays empty.

## Reference search record

The machine-readable search/rejection log is `tasks.json:reference_audit`. It records the search terms and the decision for every object. Only one 3D file is retained, with its source, pinned revision, license notice and SHA-256. No image-outline or silhouette comparison is scored.
