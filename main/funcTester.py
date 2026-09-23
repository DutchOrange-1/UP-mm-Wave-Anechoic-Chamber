import Motor_scan_A_plane as m_control
import anritsu_vectorstar_vna_interface as vna

vna.setup_vna(f_start_hz=30e9, f_stop_hz=40e9, num_points=50, ifbw_hz=1e3)
m_control.planer_scan(3, 'ECO')
