import { expect, test } from '../../testing/ui/canvas_fixtures';

interface ChatMessagePayload {
  author: string;
  text: string;
  type?: string;
  status?: string;
}

interface SuggestionVoteSummary {
  author: string;
  upvote_count: number;
  upvoters: string[];
}

interface ChatController {
  fetchChat(forceScrollToBottom?: boolean): Promise<void>;
  fetchSuggestions(): Promise<void>;
  setChatUsername(newName: string): void;
}

interface ChatControllerModule {
  initializeChatController(options: {
    messagesContainer: HTMLElement | null;
    getAuthState?: () => Promise<{ authenticated: boolean; user?: { username: string } }>;
  }): ChatController;
}

interface AcceptedSuggestionWindow extends Window {
  controller: ChatController;
}

const CHAT_CONTROLLER_MODULE_URL = '/static/generated/shared/chat-controller.js';

test('accepted suggestion remains visible alongside new pending suggestion', async ({
  canvas,
  page,
}) => {
  let messages: ChatMessagePayload[] = [
    { author: 'alice', text: 'First idea', type: 'suggestion' },
  ];
  let suggestions: SuggestionVoteSummary[] = [
    { author: 'alice', upvote_count: 1, upvoters: ['alice'] },
  ];

  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/chat',
    async (route) => {
      await route.fulfill({ json: messages });
    },
  );
  await canvas.serveRoute(
    (url) => url.origin === canvas.origin && url.pathname === '/api/suggestions',
    async (route) => {
      await route.fulfill({ json: suggestions });
    },
  );

  await canvas.mount({
    html: '<div id="chat-messages"></div>',
  });

  await page.evaluate(async (moduleUrl: string) => {
    const { initializeChatController } =
      await window.__importModule<ChatControllerModule>(moduleUrl);
    const win = window as unknown as AcceptedSuggestionWindow;
    win.controller = initializeChatController({
      messagesContainer: document.getElementById('chat-messages'),
      getAuthState: async () => ({ authenticated: false }),
    });
  }, CHAT_CONTROLLER_MODULE_URL);

  await expect(page.locator('.suggestion-upvote-btn')).toHaveCount(1);

  messages = [{ author: 'alice', text: 'First idea', type: 'suggestion', status: 'accepted' }];
  suggestions = [];
  await page.evaluate(async () => {
    const win = window as unknown as AcceptedSuggestionWindow;
    await win.controller.fetchChat();
    await win.controller.fetchSuggestions();
  });

  await expect(page.locator('.suggestion-message')).toHaveCount(1);
  await expect(page.locator('.suggestion-accepted-badge')).toHaveText('✓ Accepted');
  await expect(page.locator('.suggestion-upvote-btn')).toHaveCount(0);
  await expect(page.locator('.suggestion-withdraw-btn')).toHaveCount(0);

  messages = [
    { author: 'alice', text: 'First idea', type: 'suggestion', status: 'accepted' },
    { author: 'alice', text: 'Second idea', type: 'suggestion' },
  ];
  suggestions = [{ author: 'alice', upvote_count: 2, upvoters: ['alice', 'bob'] }];
  await page.evaluate(async () => {
    const win = window as unknown as AcceptedSuggestionWindow;
    await win.controller.fetchChat();
    win.controller.setChatUsername('alice');
  });

  await expect(page.locator('.suggestion-message')).toHaveCount(2);
  await expect(page.locator('.suggestion-accepted-badge')).toHaveCount(1);
  await expect(page.locator('.suggestion-upvote-btn')).toBeDisabled();
  await expect(page.locator('.suggestion-vote-count')).toHaveText('2 votes');
});
