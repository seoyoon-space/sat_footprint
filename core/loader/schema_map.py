"""
실제 O1B HK 구조를 반영한 패킷 정의.

각 패킷 정의:
    table       : MySQL 테이블명
    time_col    : 해당 테이블의 타임스탬프 컬럼명 (UTC 기준)
    fields      : {표준 필드명: DB 컬럼명} 매핑
    rate_hz     : (선택) 해당 패킷의 대략적인 송신 주기. asof merge tolerance 계산에 사용
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PacketSpec:
    table: str
    time_col: str
    fields: dict[str, str]
    rate_hz: float = 1.0


def _to_snake_case(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name)
    s = re.sub(r"[^0-9A-Za-z_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower()


def _field_map(*names: str) -> dict[str, str]:
    return {_to_snake_case(name): name for name in names if name and name != "timeUtc"}


HK1_FIELDS = (
    "hk1_idx",
    "timeUtc",
    "selSBband",
    "sBandPStat",
    "sBandRStat",
    "fswOpMode",
    "fswSubOpMode",
    "fswRebootCnt",
    "fswMassMem",
    "obcTemp",
    "edacCnt",
    "spDepStatXp",
    "spDepStatXm",
    "spDepStatYm",
    "spDepGpioStatXp",
    "spDepGpioStatXm",
    "spCepGpioStatYm",
    "antDepStatXp",
    "antDepStatXm",
    "spStationTempXp",
    "spStationTempXm",
    "spStationTempYm",
    "spStationTempYp",
    "spUpTopTempXp",
    "spUpTopTempXm",
    "spUpTopTempYm",
    "spUpBotTempXp",
    "spUpBotTempXm",
    "spUpBotTempYm",
    "adcEocYpZm",
    "adcEocYpZp",
    "adcEocYmZm",
    "adcEocYmZp",
    "adcAntXp",
    "adcAntXm",
    "adcPdhsCaseTemp",
    "adcEwc27CaseTemp",
    "epsDockDevFramStat",
    "epsDockDevAdc1Stat",
    "epsDockDevAdc2Stat",
    "epsDockDevAdc3Stat",
    "epsDockDevTempSenStat",
    "epsDockDevRtcStat",
    "epsMpptMode",
    "epsAntHeaterSwStat",
    "epsAcuDevFramStat",
    "epsAcuDevAdc1Stat",
    "epsAcuDevAdc2Stat",
    "epsAcuDevDac1Stat",
    "epsAcuDevDac2Stat",
    "epsAcuDevDac3Stat",
    "epsAcuDevTempSenStat",
    "bat1HeatEnableStat",
    "bat2HeatEnableStat",
    "epsPduDevFramStat",
    "epsPduDevAdc1Stat",
    "epsPduDevAdc2Stat",
    "epsPduDevTempSenStat",
    "epsPwrSwBatStat",
    "epsPwrSwObcStat",
    "epsPwrSwAocsStat",
    "epsPwrSwGpsStat",
    "epsPwrSwSBandStat",
    "epsPwrSwAntStat",
    "epsPwrSwEwc27Stat",
    "epsPwrSwMs200Stat",
    "swStatSolarPanel",
    "swStatEwc27",
    "swStatPdhs",
    "swStatEocHeatZpXp",
    "swStatEocHeatZpXm",
    "swStatEocHeatZpYp",
    "swStatEocHeatZpYm",
    "swStatOfeHeat",
    "pwrSwCurPdhsHeat",
    "pwrSwCurObc",
    "pwrSwCurAocs",
    "pwrSwCurGps",
    "pwrSwCurSBand",
    "pwrSwCurAnt",
    "pwrSwCurEwc27",
    "pwrSwCurMs200",
    "pwrSwCurSp",
    "pwrSwVolPdhsHeat",
    "pwrSwVolObc",
    "pwrSwVolAocs",
    "pwrSwVolGps",
    "pwrSwVolSBand",
    "pwrSwVolAnt",
    "pwrSwVolEwc27",
    "pwrSwVolMs200",
    "pwrSwVolSp",
    "pwrSwLatCntPdhsHeat",
    "pwrSwLatCntObc",
    "pwrSwLatCntAocs",
    "pwrSwLatCntGps",
    "pwrSwLatCntSBand",
    "pwrSwLatCntAnt",
    "pwrSwLatCntEwc27",
    "pwrSwLatCntMs200",
    "pwrSwLatCntSp",
    "dockLatCntVccAcu",
    "dockLatCntVccPdu",
    "dockLatCntVbatAcu",
    "dockLatCntVbatPdu",
    "acuCurDepXp",
    "acuCurDepXm",
    "acuCurBodyXpXm",
    "acuCurDepYm",
    "acuCurBodyYp",
    "acuCurBodyYm",
    "acuVolDepXp",
    "acuVolDepXm",
    "acuVolBodyXpXm",
    "acuVolDepYm",
    "acuVolBodyYp",
    "acuVolBodyYm",
    "dockTemp1",
    "dockTemp2",
    "pduTemp",
    "acuTemp1",
    "acuTemp2",
    "acuTemp3",
    "battery1Temp1",
    "battery1Temp2",
    "battery1Temp3",
    "battery1Temp4",
    "battery2Temp1",
    "battery2Temp2",
    "battery2Temp3",
    "battery2Temp4",
    "batteryVol",
    "chargeCur",
    "dischargeCur",
    "dockBootcause",
    "acuBootcause",
    "pduBootcause",
    "bpx1Bootcause",
    "bpx2Bootcause",
    "sBandBootcause",
    "sBandBoardTemp",
    "sBandPaTemp",
    "sBandTxCnt",
    "sBandRxCnt",
    "sBandBootCnt",
    "hkFileName",
    "regUtcTs",
)

HK2_FIELDS = (
    "hk2_idx",
    "timeUtc",
    "l0StatWord11",
    "l0StatWord12",
    "l0StatWord13",
    "l0StatWord14",
    "wdgSecondCnt",
    "momHealth1",
    "momHealth2",
    "momHealth3",
    "momHealth4",
    "momHealth5",
    "magSrcUsed",
    "timeValid",
    "refsValid",
    "attCtrlHealth1",
    "attCtrlHealth2",
    "attCtrlHealth3",
    "sttOpMode",
    "tqRodeFirPack1",
    "tqRodeFirPack2",
    "tqRodeFirPack3",
    "tqRodeFirPack4",
    "tqRodeFirPack5",
    "tqRodeFirPack6",
    "magHealth1",
    "magHealth2",
    "gpsHealth1",
    "gpsHealth2",
    "gpsHealth3",
    "gpsHealth4",
    "gpsHealth5",
    "gpsHealth6",
    "cssHealth1",
    "cssHealth2",
    "attDetHealth1",
    "attDetHealth2",
    "attDetHealth3",
    "attCmdHealth1",
    "attCmdHealth2",
    "attCmdHealth3",
    "imuHealth1",
    "imuHealth2",
    "runLowRateTask",
    "attStat",
    "tableUploadStat",
    "sunPointState",
    "taiSeconds",
    "posWrtEci1",
    "posWrtEci2",
    "posWrtEci3",
    "velWrtEci1",
    "velWrtEci2",
    "velWrtEci3",
    "qbodyWrtEci1",
    "qbodyWrtEci2",
    "qbodyWrtEci3",
    "qbodyWrtEci4",
    "filtSpeedRpm1",
    "filtSpeedRpm2",
    "filtSpeedRpm3",
    "torqDutyCycle1",
    "torqDutyCycle2",
    "torqDutyCycle3",
    "totalMomMag",
    "qEcefWrtEci1",
    "qEcefWrtEci2",
    "qEcefWrtEci3",
    "qEcefWrtEci4",
    "hkFileName",
    "regUtcTs",
)

HK3_FIELDS = (
    "hk3_idx",
    "timeUtc",
    "startupCause1",
    "startupCause2",
    "startupCause3",
    "startupCause4",
    "startupCause5",
    "startupCause6",
    "startupCause7",
    "startupCause8",
    "startupCause9",
    "startupCause10",
    "stateOfUnit",
    "selfTestRet1",
    "selfTestRet2",
    "selfTestRet3",
    "temperature",
    "hkFileName",
    "regUtcTs",
)

HK4_FIELDS = (
    "hk4_idx",
    "timeUtc",
    "cmdStat",
    "errStat",
    "revCmdCnt",
    "revCmdErrCnt",
    "temperature",
    "storageCapa",
    "hkFileName",
    "regUtcTs",
)

HK5_FIELDS = (
    "hk5_idx",
    "timeUtc",
    "sohSessStat",
    "sohConfStat",
    "sohSenStat",
    "sohCaptureStat",
    "sohReadOutStat",
    "ceVFeeSmps",
    "ceCFeeSmps",
    "ceCFeeLdo",
    "ceCBrd5v0",
    "ceVFeeLdo",
    "ceCSmps3v3",
    "ceCSmps1v0",
    "ceCSmps1v2",
    "ceCSdramVtt",
    "ceTFpga",
    "storageCapa",
    "feeTSenDiode",
    "feeTSenDigital",
    "ofeNxny34",
    "ofeNxny140",
    "ofeNxny246",
    "ofeNxpy34",
    "ofeNxpy140",
    "ofeNxpy246",
    "ofePxpy34",
    "ofePxpy140",
    "ofePxpy246",
    "ofePxny34",
    "ofePxny140",
    "ofePxny246",
    "ofePx270",
    "ofePy270",
    "ofeNx270",
    "ofeNy270",
    "hkFileName",
    "regUtcTs",
)

HK6_FIELDS = (
    "hk6_idx",
    "timeUtc",
    "l0AdcWord1Ch0",
    "l0AdcWord1Ch1",
    "l0AdcWord2Ch2",
    "l0AdcWord2Ch3",
    "l0AdcWord3Ch4",
    "l0AdcWord3Ch5",
    "l0AdcWord4Ch6",
    "l0AdcWord4Ch7",
    "l0CmdTimeTag",
    "l0StatusWord2",
    "cmdAcceptCnt",
    "cmdRejectCnt",
    "lastAcceptCmdBytes1",
    "lastAcceptCmdBytes2",
    "lastRejCmdBytes1",
    "lastRejCmdBytes2",
    "diaInertia1",
    "diaInertia2",
    "diaInertia3",
    "badAttTimer",
    "badRateTimer",
    "cssInvalidCnt",
    "imuInvalidCnt",
    "imuReinitCnt",
    "residual1",
    "residual2",
    "residual3",
    "bodyRate1",
    "bodyRate2",
    "bodyRate3",
    "gyroBiasEst1",
    "gyroBiasEst2",
    "gyroBiasEst3",
    "cmdQBodyWrtEci1",
    "cmdQBodyWrtEci2",
    "cmdQBodyWrtEci3",
    "cmdQBodyWrtEci4",
    "cmdSun1",
    "cmdSun2",
    "cmdSun3",
    "dragEst1",
    "dragEst2",
    "dragEst3",
    "motorFaultCnt1",
    "motorFaultCnt2",
    "motorFaultCnt3",
    "sttMedianNoiseBlks",
    "medianBckgnd",
    "detTimeoutCnt",
    "numAttitudeStars",
    "eigenErr",
    "sunVecBody1",
    "sunVecBody2",
    "sunVecBody3",
    "magVecBody1",
    "magVecBody2",
    "magVecBody3",
    "rawSunSenData1",
    "rawSunSenData2",
    "rawSunSenData3",
    "rawSunSenData4",
    "rawSunSenData5",
    "rawSunSenData6",
    "rawSunSenData7",
    "rawSunSenData8",
    "rawSunSenData9",
    "rawSunSenData10",
    "rawSunSenData11",
    "rawSunSenData12",
    "rawMagData1",
    "rawMagData2",
    "rawMagData3",
    "rawMagData4",
    "rawMagData5",
    "rawMagData6",
    "rawMagData7",
    "rawMagData8",
    "rawMagData9",
    "imuAvgVec1",
    "imuAvgVec2",
    "imuAvgVec3",
    "imuAvgVecFrame",
    "hrRunCnt",
    "hrTimeUsec",
    "detTemp",
    "imuTemp",
    "motorTemp1",
    "motorTemp2",
    "motorTemp3",
    "digitalBusV",
    "motorBusV",
    "rodBusV",
    "gpsCycSinCrcData",
    "gpsCycSinLatestData",
    "gpsLockCnt",
    "avgTimeTag",
    "hkFileName",
    "regUtcTs",
)


# 데이터는 hk1~hk6 각각 실제 컬럼명을 그대로 사용, 병합 로직이 예측 가능
# canonical 이름으로 매핑되도록 실제 HK 정의서 기준으로 구성.
# 실제 DB 스키마는 MySQL schema 'nstanl' 안에 tbl_obs1a_hk1 / tbl_obs1a_hk2 ... 형식으로
# 구성되어 있으며, 시간값은 Unix epoch UTC(초) 기준으로 저장됩니다.
# 따라서 table 이름과 time 컬럼을 실제 구조에 맞춰 재정의한다.
#
# O1A/O1B는 같은 버스 설계라 hk1~hk6 컬럼 구조가 동일하고, 같은 DB('nstanl') 안에서
# 위성별로 테이블만 분리되어 있다(tbl_obs1a_hk* / tbl_obs1b_hk*) - 별도 DB 인스턴스가
# 아니라 테이블 접두어만 다름(SHOW TABLES 결과로 확인됨, posWrtEci/velWrtEci/qbodyWrtEci
# 등 세부 컬럼도 동일). 이전 버전은 이 사실을 반영하지 못해 satellite_id와 무관하게
# 항상 tbl_obs1a_hk*만 조회했다 - O1B 요청이 실제로는 O1A 데이터를 반환하던 버그였음.
HK1_FIELDS_MAP = _field_map(*HK1_FIELDS)
HK2_FIELDS_MAP = _field_map(*HK2_FIELDS)
HK3_FIELDS_MAP = _field_map(*HK3_FIELDS)
HK4_FIELDS_MAP = _field_map(*HK4_FIELDS)
HK5_FIELDS_MAP = _field_map(*HK5_FIELDS)
HK6_FIELDS_MAP = _field_map(*HK6_FIELDS)

# satellite_id -> 테이블명에 쓰이는 접두어 (tbl_{prefix}_hk1 ...)
SATELLITE_TABLE_PREFIX: dict[str, str] = {
    "O1A": "obs1a",
    "O1B": "obs1b",
}


def _build_packet_schema(table_prefix: str) -> dict[str, PacketSpec]:
    return {
        "hk1": PacketSpec(table=f"tbl_{table_prefix}_hk1", time_col="timeUtc", fields=HK1_FIELDS_MAP, rate_hz=1.0),
        "hk2": PacketSpec(table=f"tbl_{table_prefix}_hk2", time_col="timeUtc", fields=HK2_FIELDS_MAP, rate_hz=1.0),
        "hk3": PacketSpec(table=f"tbl_{table_prefix}_hk3", time_col="timeUtc", fields=HK3_FIELDS_MAP, rate_hz=1.0),
        "hk4": PacketSpec(table=f"tbl_{table_prefix}_hk4", time_col="timeUtc", fields=HK4_FIELDS_MAP, rate_hz=1.0),
        "hk5": PacketSpec(table=f"tbl_{table_prefix}_hk5", time_col="timeUtc", fields=HK5_FIELDS_MAP, rate_hz=1.0),
        "hk6": PacketSpec(table=f"tbl_{table_prefix}_hk6", time_col="timeUtc", fields=HK6_FIELDS_MAP, rate_hz=1.0),
    }


HK_PACKET_SCHEMA_BY_SATELLITE: dict[str, dict[str, PacketSpec]] = {
    sat_id: _build_packet_schema(prefix) for sat_id, prefix in SATELLITE_TABLE_PREFIX.items()
}

# 하위 호환 기본값(O1A) - satellite_id를 안 넘기는 기존 호출부용.
HK_PACKET_SCHEMA: dict[str, PacketSpec] = HK_PACKET_SCHEMA_BY_SATELLITE["O1A"]


def get_hk_packet_schema(satellite_id: str | None) -> dict[str, PacketSpec]:
    """satellite_id(O1A/O1B)에 맞는 hk1~hk6 PacketSpec 세트를 반환. None이면 O1A 기본값."""
    if not satellite_id:
        return HK_PACKET_SCHEMA
    sat = satellite_id.upper()
    if sat not in HK_PACKET_SCHEMA_BY_SATELLITE:
        raise ValueError(
            f"No HK packet schema registered for satellite_id='{sat}'. "
            f"Available: {list(HK_PACKET_SCHEMA_BY_SATELLITE)}"
        )
    return HK_PACKET_SCHEMA_BY_SATELLITE[sat]


MASTER_PACKET = "hk1"
DEFAULT_MERGE_TOLERANCE_SEC = 1.0