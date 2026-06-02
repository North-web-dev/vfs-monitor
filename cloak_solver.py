import cloakbrowser as cb, time
LOGIN_PAGE='https://visa.vfsglobal.com/uzb/en/lva/login'
def solve_sync(proxy):
    br=None
    try:
        br=cb.launch(headless=True, proxy=proxy)
        pg=br.new_page()
        pg.goto(LOGIN_PAGE, timeout=45000, wait_until='domcontentloaded')
        for i in range(40):
            el=pg.query_selector('[name=cf-turnstile-response]')
            try:
                v=el.input_value() if el else ''
                if v and len(v)>20: return v
            except Exception: pass
            time.sleep(2)
        return None
    finally:
        try:
            if br: br.close()
        except Exception: pass
