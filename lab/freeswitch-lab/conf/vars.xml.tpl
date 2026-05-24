<!--
  lab/freeswitch-lab/conf/vars.xml.tpl
  Template for FreeSWITCH global variables — LAB version.

  Processed by freeswitch/docker-entrypoint.sh via envsubst.
  DO NOT EDIT vars.xml directly — it is generated from this template.

  Differences from production template (freeswitch/conf/vars.xml.tpl):
  - PCMA (alaw) first in codec prefs (Fanvil phones require this)
  - voip_default_gateway = asterisk-lab (set via VOIP_DEFAULT_GATEWAY env)
  - lab_asterisk_ip hardcoded (172.28.0.50 — fixed in docker-compose.lab.yml)
  - Voxbone vars not used; kept to avoid entrypoint warnings

  Variables substituted by entrypoint (see docker-entrypoint.sh envsubst list):
    ${VOIP_API_BASE_URL}          http://127.0.0.1:8001
    ${VOIP_INTERNAL_TOKEN}        from .env.lab INTERNAL_TOKEN
    ${VOIP_DEFAULT_GATEWAY}       asterisk-lab
    ${VOXBONE_USERNAME/PASSWORD/REALM/PROXY}  irrelevant in lab, set to "lab"
    ${RTP_START_PORT}             16384
    ${RTP_END_PORT}               32768
    ... (all other standard vars)
-->
<include>

  <!-- FastAPI endpoint — port 8001 on host (avoids portainer:8000 conflict) -->
  <X-PRE-PROCESS cmd="set" data="voip_api_base_url=${VOIP_API_BASE_URL}"/>

  <!-- Internal service token -->
  <X-PRE-PROCESS cmd="set" data="voip_internal_token=${VOIP_INTERNAL_TOKEN}"/>

  <!-- Sound file paths -->
  <X-PRE-PROCESS cmd="set" data="voip_sounds_base=${VOIP_SOUNDS_BASE}"/>
  <X-PRE-PROCESS cmd="set" data="voip_digits_base=${VOIP_DIGITS_BASE}"/>

  <!-- IVR tuning -->
  <X-PRE-PROCESS cmd="set" data="voip_auth_attempts=${VOIP_AUTH_ATTEMPTS}"/>
  <X-PRE-PROCESS cmd="set" data="voip_pin_length=${VOIP_PIN_LENGTH}"/>
  <X-PRE-PROCESS cmd="set" data="voip_dtmf_timeout_ms=${VOIP_DTMF_TIMEOUT_MS}"/>

  <!-- HTTP timeouts (ms) — R-SIP-01: auth ≤ 2000ms -->
  <X-PRE-PROCESS cmd="set" data="voip_http_timeout_ms=${VOIP_HTTP_TIMEOUT_MS}"/>
  <X-PRE-PROCESS cmd="set" data="voip_tick_timeout_ms=${VOIP_TICK_TIMEOUT_MS}"/>

  <!-- Credit warning thresholds -->
  <X-PRE-PROCESS cmd="set" data="voip_warn_remaining_seconds=${VOIP_WARN_REMAINING_SEC}"/>
  <X-PRE-PROCESS cmd="set" data="voip_beep_remaining_seconds=${VOIP_BEEP_REMAINING_SEC}"/>

  <!-- LAB: default gateway is asterisk-lab (injected via VOIP_DEFAULT_GATEWAY env) -->
  <X-PRE-PROCESS cmd="set" data="voip_default_gateway=${VOIP_DEFAULT_GATEWAY}"/>

  <!-- Not used in lab but kept to avoid entrypoint warnings -->
  <X-PRE-PROCESS cmd="set" data="voxbone_username=${VOXBONE_USERNAME}"/>
  <X-PRE-PROCESS cmd="set" data="voxbone_password=${VOXBONE_PASSWORD}"/>
  <X-PRE-PROCESS cmd="set" data="voxbone_realm=${VOXBONE_REALM}"/>
  <X-PRE-PROCESS cmd="set" data="voxbone_proxy=${VOXBONE_PROXY}"/>

  <!-- LAB CODEC: PCMA (alaw) first — Fanvil phones prefer G.711a -->
  <!-- Production default is PCMU first; we swap for Fanvil compatibility -->
  <X-PRE-PROCESS cmd="set" data="global_codec_prefs=PCMA,PCMU,G729,OPUS"/>
  <X-PRE-PROCESS cmd="set" data="outbound_codec_prefs=PCMA,PCMU"/>

  <!-- RTP port range -->
  <X-PRE-PROCESS cmd="set" data="rtp_start_port=${RTP_START_PORT}"/>
  <X-PRE-PROCESS cmd="set" data="rtp_end_port=${RTP_END_PORT}"/>

  <!-- LAB: Asterisk carrier IP (hardcoded — Docker bridge fixed IP) -->
  <!-- Used in freeswitch-lab/conf/sip_profiles/external.xml gateway config -->
  <X-PRE-PROCESS cmd="set" data="lab_asterisk_ip=172.28.0.50"/>
  <X-PRE-PROCESS cmd="set" data="lab_asterisk_port=5060"/>

  <!-- Domain: resolves to host machine IP — Fanvil phones register to this -->
  <X-PRE-PROCESS cmd="set" data="domain=$${local_ip_v4}"/>
  <X-PRE-PROCESS cmd="set" data="default_provider=$${voip_default_gateway}"/>

</include>
