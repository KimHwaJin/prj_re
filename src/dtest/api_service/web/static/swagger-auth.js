const apiPrefix = PREFIX;
let csrfToken = null;
const status = () => document.getElementById('sso-status');
async function refreshIdentity() {
  csrfToken = null;
  try {
    const response = await fetch(apiPrefix + '/users/me', {credentials:'same-origin', cache:'no-store'});
    if (!response.ok) {
      status().textContent = response.status === 401 ? '로그인이 필요합니다' : '로그인 상태 조회 실패';
      return false;
    }
    const user = await response.json();
    csrfToken = user.csrf_token;
    status().textContent = user.user_name + ' · ' + user.role;
    return true;
  } catch (_) { status().textContent = '로그인 상태 조회 실패'; return false; }
}
async function ssoRequestInterceptor(request) {
  const url = new URL(request.url, window.location.href);
  // Never send the CSRF secret to a remote specification or another API origin.
  if (url.origin !== window.location.origin || !url.pathname.startsWith(apiPrefix + '/')) return request;
  request.credentials = 'same-origin';
  if (!['GET','HEAD','OPTIONS'].includes((request.method || 'GET').toUpperCase())) {
    if (!csrfToken && !(await refreshIdentity())) throw new Error('SSO 로그인 후 로그인 상태 확인 버튼을 누르세요.');
    request.headers = request.headers || {};
    request.headers['X-CSRF-Token'] = csrfToken;
  }
  return request;
}
