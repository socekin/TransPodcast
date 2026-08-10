# Modal Speech Applications

The Modal applications under `experiments/pipeline/` pin the speech workload
entry points used by the TransPodcast host. Their deployment downloads model assets
and can incur storage or compute charges.

## Preparation

1. Install the locked dependencies with `make setup`.
2. Authenticate a dedicated Modal profile and create the environment:

   ```shell
   uv run modal token new --profile transpodcast
   MODAL_PROFILE=transpodcast uv run modal environment create transpodcast
   MODAL_PROFILE=transpodcast uv run modal token info
   ```

   If the environment already exists, skip its creation. See the official
   [Modal token](https://modal.com/docs/cli/latest/token) and
   [environment](https://modal.com/docs/guide/environments) documentation.
3. Review every model ID and its terms in [Third-party models](../model-licenses.md).
4. Set provider-side spending limits.
5. Confirm the source and voice-reference data may be sent to Modal.

## Deploy

From the repository root:

```shell
./deploy/modal-deploy.sh
```

The script deploys changed applications and records deployment state under the
ignored `data/` directory. Optional multi-profile routing exists for separate
credit pools, but it does not replace provider-side budgets.

Configure `TRANSPODCAST_MODAL_COMMAND` only after deployment. Verify Setup
Readiness before starting a real Processing Run.
