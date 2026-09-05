import { describe, expect, test } from 'bun:test';
import {
  claudeToResource,
  codexToResource,
  openaiToResource,
} from '../src/features/providers/adapters';
import { PROVIDER_BRAND_ORDER } from '../src/features/providers/descriptors';
import {
  buildCode0Raw,
  CODE0_OPENAI_BASE_URL,
  CODE0_PROVIDER_NAME,
} from '../src/features/providers/code0';
import {
  buildQiniuCloudRaw,
  QINIU_CLOUD_BASE_URL_OPTIONS,
  QINIU_CLOUD_PROVIDER_NAME,
} from '../src/features/providers/qiniuCloud';
import { normalizeConfigResponse } from '../src/services/api/transformers';

const openAIConfig = (name: string, baseUrl: string) => ({
  openaiCompatibility: [
    {
      name,
      baseUrl,
      apiKeyEntries: [{ apiKey: 'test-key' }],
    },
  ],
});

const customOpenAIConfig = (name: string) => openAIConfig(name, 'https://gateway.example.com/v1');

const mixedOpenAIConfig = (name: string, officialBaseUrl: string) => ({
  openaiCompatibility: [
    {
      name,
      baseUrl: officialBaseUrl,
      apiKeyEntries: [{ apiKey: 'official-key' }],
    },
    {
      name,
      baseUrl: 'https://gateway.example.com/v1',
      apiKeyEntries: [{ apiKey: 'custom-key' }],
    },
  ],
});

describe('sponsor custom endpoint isolation', () => {
  test('removes APIKEY.FUN branding while preserving ordinary protocol resources', () => {
    expect(PROVIDER_BRAND_ORDER).not.toContain('apikeyFun');
    for (const host of ['api.apikey.fun', 'slb.apikey.fun']) {
      const openai = {
        name: 'apikeyFun',
        baseUrl: `https://${host}/v1`,
        apiKeyEntries: [{ apiKey: 'test-key' }],
        sourceIndex: 3,
      };
      const openaiResource = openaiToResource(openai, 0);
      expect(openaiResource.brand).toBe('openaiCompatibility');
      expect(openaiResource.selector).toEqual({
        brand: 'openaiCompatibility',
        name: 'apikeyFun',
        index: 3,
      });
      expect(openaiResource.raw).toBe(openai);

      for (const [adapt, brand, baseUrl] of [
        [claudeToResource, 'claude', `https://${host}`],
        [codexToResource, 'codex', `https://${host}/v1`],
      ] as const) {
        const config = { apiKey: 'test-key', baseUrl };
        const resource = adapt(config, 2);
        expect(resource.brand).toBe(brand);
        expect(resource.selector).toEqual({ brand, apiKey: 'test-key', baseUrl, index: 2 });
        expect(resource.raw).toBe(config);
      }
    }
  });

  test('keeps Code0-named custom endpoints in the generic OpenAI group', () => {
    expect(buildCode0Raw(customOpenAIConfig(CODE0_PROVIDER_NAME)).openai).toEqual([]);
  });

  test('keeps Qiniu-named custom endpoints in the generic OpenAI group', () => {
    expect(buildQiniuCloudRaw(customOpenAIConfig(QINIU_CLOUD_PROVIDER_NAME)).openai).toEqual([]);
  });

  test('keeps same-name custom entries outside sponsor delete targets', () => {
    expect(
      buildCode0Raw(mixedOpenAIConfig(CODE0_PROVIDER_NAME, CODE0_OPENAI_BASE_URL)).openai.map(
        (item) => item.index
      )
    ).toEqual([0]);
    expect(
      buildQiniuCloudRaw(
        mixedOpenAIConfig(QINIU_CLOUD_PROVIDER_NAME, QINIU_CLOUD_BASE_URL_OPTIONS[0].openaiBaseUrl)
      ).openai.map((item) => item.index)
    ).toEqual([0]);
  });

  test('keeps backend indexes when normalization filters an unnamed item', () => {
    const config = normalizeConfigResponse({
      'openai-compatibility': [
        { 'base-url': 'https://invalid.example.com/v1' },
        {
          name: CODE0_PROVIDER_NAME,
          'base-url': CODE0_OPENAI_BASE_URL,
          'api-key-entries': [{ 'api-key': 'official-a' }],
        },
        {
          name: CODE0_PROVIDER_NAME,
          'base-url': 'https://gateway.example.com/v1',
          'api-key-entries': [{ 'api-key': 'custom-key' }],
        },
        {
          name: CODE0_PROVIDER_NAME,
          'base-url': CODE0_OPENAI_BASE_URL,
          'api-key-entries': [{ 'api-key': 'official-b' }],
        },
      ],
    });

    expect(config.openaiCompatibility?.map((item) => item.sourceIndex)).toEqual([1, 2, 3]);
    expect(buildCode0Raw(config).openai.map((item) => item.index)).toEqual([1, 3]);
    expect(openaiToResource(config.openaiCompatibility![1], 1).originalIndex).toBe(2);
  });

  test('still aggregates each sponsor official OpenAI endpoint', () => {
    expect(buildCode0Raw(openAIConfig('custom-name', CODE0_OPENAI_BASE_URL)).openai.length).toBe(1);
    expect(
      buildQiniuCloudRaw(openAIConfig('custom-name', QINIU_CLOUD_BASE_URL_OPTIONS[0].openaiBaseUrl))
        .openai.length
    ).toBe(1);
  });
});
