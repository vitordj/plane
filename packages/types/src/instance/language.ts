/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

/**
 * Orca (fork): instance configuration keys for the interface language.
 *
 * Its own module, mirroring how upstream splits `ai`, `email` and `image`, so
 * the fork's key joins `TInstanceConfigurationKeys` through a one-line union
 * member rather than by editing an upstream list.
 */
export type TInstanceLanguageConfigurationKeys = "DEFAULT_LANGUAGE";
