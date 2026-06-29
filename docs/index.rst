.. SPDX-License-Identifier: copyleft-next-0.3.1

==========
kdevops-ng
==========

kdevops-ng is a Linux kernel development and test-automation framework built
on a self-hosted Windmill workflow engine, with Nix supplying reproducible
build and guest environments. It runs on any Linux distribution with systemd
and Nix; see :doc:`getting-started/requirements`.

.. note::

   kdevops-ng is a proof of concept intended to be merged upstream into
   kdevops after community discussion. See :ref:`project-status` for the
   staged path and a link to the mailing-list thread.

Community
=========

- Source and issues: `GitHub <https://github.com/dagomez137/kdevops-ng>`__.
- Chat: `Discord <https://bit.ly/linux-kdevops-chat>`__, or ``#kdevops`` on
  `OFTC IRC <https://webchat.oftc.net/?channels=kdevops>`__.
- Mailing list: ``kdevops@lists.linux.dev`` (on ``lists.linux.dev``).

These are linked from the icons in the top navigation bar too.

.. toctree::
   :maxdepth: 2

   getting-started/index
   concepts/index
   flows/index
   reference/index
   deployment/index
   contributing/index
   roadmap

.. toctree::
   :maxdepth: 1
   :caption: Pending review

   staging
