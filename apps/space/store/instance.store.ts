/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { set } from "lodash-es";
import { observable, action, makeObservable, runInAction } from "mobx";
// plane imports
import { applyDefaultLanguage } from "@plane/i18n";
import type { TLanguage } from "@plane/i18n";
import { InstanceService } from "@plane/services";
import type { IInstance, IInstanceConfig } from "@plane/types";
// store
import type { RootStore } from "@/store/root.store";

type TError = {
  status: string;
  message: string;
  data?: {
    is_activated: boolean;
    is_setup_done: boolean;
  };
};

export interface IInstanceStore {
  // observables
  isLoading: boolean;
  instance: IInstance | undefined;
  config: IInstanceConfig | undefined;
  error: TError | undefined;
  // action
  fetchInstanceInfo: () => Promise<void>;
  hydrate: (data: IInstance) => void;
}

export class InstanceStore implements IInstanceStore {
  isLoading: boolean = true;
  instance: IInstance | undefined = undefined;
  config: IInstanceConfig | undefined = undefined;
  error: TError | undefined = undefined;
  // services
  instanceService;

  constructor(private store: RootStore) {
    makeObservable(this, {
      // observable
      isLoading: observable.ref,
      instance: observable,
      config: observable,
      error: observable,
      // actions
      fetchInstanceInfo: action,
      hydrate: action,
    });
    // services
    this.instanceService = new InstanceService();
  }

  hydrate = (data: IInstance) => set(this, "instance", data);

  /**
   * @description fetching instance information
   */
  fetchInstanceInfo = async () => {
    try {
      this.isLoading = true;
      this.error = undefined;
      const instanceInfo = await this.instanceService.info();
      runInAction(() => {
        this.isLoading = false;
        this.instance = instanceInfo.instance;
        this.config = instanceInfo.config;
      });
      // Orca (fork): published pages have no signed-in viewer to read a
      // preference from, so the organization's default is the only signal
      // there is. Mirrors the web app's instance store.
      void applyDefaultLanguage(instanceInfo.config?.default_language as TLanguage | undefined);
    } catch (_error) {
      runInAction(() => {
        this.isLoading = false;
        this.error = {
          status: "error",
          message: "Failed to fetch instance info",
        };
      });
    }
  };
}
