const NOT_FOUND_PATTERNS = [
  /^(?:not[\s_-]+found\b|(?:page|resource|document)\s+(?:is\s+|was\s+)?not[\s_-]+found\b|(?:the\s+)?(?:page|resource|document)(?:\s+you(?:'re| are) looking for)?\s+(?:(?:could not|couldn't|cannot|can't)\s+be\s+found|not[\s_-]+found)\b)/i,
  /^(?:the\s+)?(?:requested\s+)?(?:page|resource|document)(?:\s+you(?:'re| are) looking for)?\s+(?:does not|doesn't|doesnt)\s+exist\b/i,
  /^(?:we\s+)?(?:could not|couldn't|couldnt|can't|cannot)\s+find\s+(?:the\s+|that\s+|this\s+)?(?:requested\s+)?(?:page|resource|document)\b/i,
  /^(?:this\s+)?(?:page|resource|document)\s+(?:is|was)\s+not\s+available\b/i,
];

const GENERIC_404_PATTERN = /^404\b/i;
const BRANDED_404_PATTERN =
  /^404\b(?:\s*:\s*not[\s_-]*found\b|\s+(?:page\s+)?not[\s_-]+found\b)/i;

const MAX_BODY_MESSAGE_LENGTH = 200;

// Body copy is weaker evidence than a title or heading, so only short, explicit
// messages gathered from visible main-content paragraphs or live error regions qualify.
function isNotFoundLabel(value) {
  if (typeof value !== 'string') return false;
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (
    GENERIC_404_PATTERN.test(normalized) ||
    NOT_FOUND_PATTERNS.some(pattern => pattern.test(normalized))
  ) return true;
  const brandedSuffixes = normalized.split(/\s+(?:\||—|–|-)\s+/).slice(1);
  return brandedSuffixes.some(section =>
    BRANDED_404_PATTERN.test(section) ||
    NOT_FOUND_PATTERNS.some(pattern => pattern.test(section))
  );
}

export function findNotFoundBodyMessage(bodyMessages = []) {
  if (!Array.isArray(bodyMessages)) return null;
  return bodyMessages.find(value => {
    if (typeof value !== 'string') return false;
    const normalized = value.replace(/\s+/g, ' ').trim();
    return normalized.length <= MAX_BODY_MESSAGE_LENGTH &&
      (BRANDED_404_PATTERN.test(normalized) ||
        NOT_FOUND_PATTERNS.some(pattern => pattern.test(normalized)));
  }) ?? null;
}

export function isNotFoundDocument(title, heading, bodyMessages = []) {
  if ([title, heading].some(isNotFoundLabel)) return true;
  return findNotFoundBodyMessage(bodyMessages) !== null;
}
