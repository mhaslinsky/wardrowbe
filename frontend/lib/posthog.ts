import type { PostHog, PostHogConfig } from 'posthog-js';

export interface PostHogRuntimeConfig {
  key?: string;
  host?: string;
  unmaskedHosts?: string;
}

export const measurementSelector = '[data-posthog-measurements]';
export const imageSelector = 'img, picture, canvas, video';

export function recordingOptions(hostname: string, unmaskedHosts = '') {
  const unmasked = unmaskedHosts.split(',').some((host) =>
    host.trim().toLowerCase() === hostname.toLowerCase(),
  );
  return unmasked ? {} : {
    blockSelector: imageSelector,
    maskTextSelector: `${measurementSelector}, ${measurementSelector} *`,
  };
}

let client: PostHog | undefined;

export async function initializePostHog(config: PostHogRuntimeConfig, hostname: string) {
  if (!config.key) return;
  const { default: posthog } = await import('posthog-js');
  if (!client) {
    const options: Partial<PostHogConfig> = {
      api_host: config.host || 'https://us.i.posthog.com',
      capture_exceptions: true,
      disable_session_recording: false,
      session_recording: {
        maskAllInputs: true,
        ...recordingOptions(hostname, config.unmaskedHosts),
      },
    };
    posthog.init(config.key, options);
    client = posthog;
  }
  return client;
}

export function resetPostHog() {
  client?.reset();
}
