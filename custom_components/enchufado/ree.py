"""REE/ESIOS API client for PVPC prices.

Derived from pvpc_energy by yinyang17 (https://github.com/yinyang17/pvpc_energy).
"""
import datetime
import math
import aiohttp
import logging

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=15)


class REE:
    _url = "https://api.esios.ree.es/indicators/1001?geo_ids[]=8741&start_date={start_date}&end_date={end_date}"

    @staticmethod
    def _headers(token):
        return {
            "Accept": "application/json; application/vnd.esios-api-v2+json",
            "Content-Type": "application/json",
            "Host": "api.esios.ree.es",
            "x-api-key": token,
        }

    @staticmethod
    async def pvpc(start_date, end_date, token):
        """Hourly PVPC prices as {unix_ts: €/kWh}; None on hard failure, {} if empty.

        Negative prices (real in the spot market) are kept. Malformed entries
        are skipped, and a payload without the expected structure is treated
        as a hard failure so the caller stops the chunk loop.
        """
        _LOGGER.debug("REE.pvpc: %s -> %s", start_date.isoformat(), end_date.isoformat())
        url = REE._url.format(
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%dT23%%3A00%%3A00"),
        )
        response = None
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.get(url, headers=REE._headers(token)) as resp:
                    if resp.status == 200:
                        try:
                            response = await resp.json(content_type=None)
                        except ValueError:
                            _LOGGER.warning("REE.pvpc: 200 with non-JSON body")
                            return None
                    else:
                        _LOGGER.warning("REE.pvpc: unexpected status %s", resp.status)
                        return None
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("REE.pvpc request failed: %s", err)
            return None

        values = response.get("indicator", {}).get("values") if isinstance(response, dict) else None
        if values is None:
            _LOGGER.warning("REE.pvpc: malformed payload (keys: %s)",
                            list(response) if isinstance(response, dict) else type(response).__name__)
            return None

        result = {}
        for value in values:
            try:
                ts = int(datetime.datetime.fromisoformat(value["datetime"]).timestamp())
                kwh_price = round(float(value["value"]) / 1000, 5)  # MWh → €/kWh
                if not math.isfinite(kwh_price):
                    continue
                result[ts] = kwh_price
            except (KeyError, TypeError, ValueError):
                continue
        _LOGGER.debug("REE.pvpc: got %d price records", len(result))
        return result
