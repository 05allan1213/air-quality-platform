import { randomBytes, randomUUID } from 'node:crypto'

// The authenticated flows exercise the normal registration and password forms.
// Give the two fields independent entropy and lengths so Django's similarity
// validator cannot confuse two random UUID-shaped strings.
export const freshPassword = () => 'Aq!' + randomBytes(72).toString('base64url')

export const freshReader = () => ({ username: `s08-${randomUUID()}`, password: freshPassword() })
