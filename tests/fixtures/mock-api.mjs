// Used only by child-process CLI tests. No request can reach the network.
import { appendFileSync } from 'node:fs';
globalThis.fetch = async (input, init) => {
  const url = String(input);
  if (url.includes('api.telegram.org')) {
    const method = url.split('/').at(-1);
    appendFileSync(process.env.MOCK_REQUESTS, JSON.stringify({ method, params: Object.fromEntries(new URLSearchParams(String(init?.body))) }) + '\n');
    if (method === 'setMyCommands') return Response.json({ ok: true, result: true });
    if (method === 'getUpdates') return Response.json({ ok: true, result: [] });
    if (method === 'sendMessage') {
      if (process.env.MOCK_SCENARIO === 'failure') throw new Error('offline');
      if (process.env.MOCK_SCENARIO === 'continuous') setTimeout(() => {
        // Windows cannot send POSIX SIGINT to a child; exercise its installed handler.
        if (process.platform === 'win32') process.emit('SIGINT');
        else process.kill(process.pid, 'SIGINT');
      }, 20);
      return Response.json({ ok: true, result: {} });
    }
  }
  if (url.startsWith('https://example.test/')) return Response.json({ result: { projects: [] } });
  throw new Error('Unexpected request in offline CLI test');
};
